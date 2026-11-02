# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario suite definition API routes."""

from typing import Annotated
from uuid import UUID

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Depends, HTTPException, Request, status

from pyrit.backend.middleware.auth import current_user_name, require_admin
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scenario_suites import (
    ScenarioSuiteListResponse,
    ScenarioSuiteResponse,
    UpdateScenarioSuiteRequest,
)
from pyrit.backend.services.scenario_suite_execution_service import (
    ScenarioSuiteExecutionCapacityError,
    get_scenario_suite_execution_service,
)
from pyrit.backend.services.scenario_suite_service import get_scenario_suite_service
from pyrit.models import LaunchScenarioSuiteRequest, ScenarioSuiteExecutionSummary
from pyrit.models.catalog import ScenarioSuite
from pyrit.registry import (
    ScenarioSuiteConflictError,
    ScenarioSuiteExecutionNotFoundError,
    ScenarioSuiteLaunchConflictError,
    ScenarioSuiteNameRegisteredError,
    ScenarioSuiteNotRunnableError,
)

router = APIRouter(prefix="/scenario-suites", tags=["scenario-suites"])


def _storage_unavailable() -> HTTPException:
    """
    Create a sanitized response for unavailable suite storage.

    Returns:
        HTTPException: A service-unavailable response without SDK details.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Scenario suite storage is temporarily unavailable",
    )


def _not_found(name: str) -> HTTPException:
    """
    Create a not-found response for a missing suite.

    Returns:
        HTTPException: A not-found response naming the suite.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scenario suite '{name}' not found")


async def _load_suite_or_404_async(name: str) -> ScenarioSuiteResponse:
    """
    Read one suite, translating storage failures into HTTP responses.

    Returns:
        ScenarioSuiteResponse: The suite and current entry statuses.

    Raises:
        HTTPException: 404 if absent, 400 for an illegal name, or 503 if storage fails.
    """
    try:
        suite = await get_scenario_suite_service().get_suite_async(name=name)
    except AzureError as exc:
        raise _storage_unavailable() from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    if suite is None:
        raise _not_found(name)
    return suite


@router.get("", response_model=ScenarioSuiteListResponse)
async def list_scenario_suites() -> ScenarioSuiteListResponse:  # pyrit-async-suffix-exempt
    """
    List every readable stored or registered scenario suite.

    Returns:
        ScenarioSuiteListResponse: Suites from both origins with current entry statuses.
    """
    try:
        return await get_scenario_suite_service().list_suites_async()
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.get(
    "/executions/{execution_id}",
    response_model=ScenarioSuiteExecutionSummary,
    responses={404: {"model": ProblemDetail, "description": "Transient suite execution not found"}},
)
async def get_scenario_suite_execution(  # pyrit-async-suffix-exempt
    execution_id: UUID,
) -> ScenarioSuiteExecutionSummary:
    """
    Get process-local progress and grouped child runs for one suite execution.

    Returns:
        ScenarioSuiteExecutionSummary: Current transient suite progress.
    """
    execution = await get_scenario_suite_execution_service().get_execution_async(execution_id=execution_id)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scenario suite execution '{execution_id}' is no longer available",
        )
    return execution


@router.post(
    "/executions/{execution_id}/cancel",
    response_model=ScenarioSuiteExecutionSummary,
    responses={404: {"model": ProblemDetail, "description": "Transient suite execution not found"}},
)
async def cancel_scenario_suite_execution(  # pyrit-async-suffix-exempt
    execution_id: UUID,
) -> ScenarioSuiteExecutionSummary:
    """
    Best-effort cancel pending submissions and child scenario runs.

    Returns:
        ScenarioSuiteExecutionSummary: Updated transient suite progress.
    """
    execution = await get_scenario_suite_execution_service().cancel_execution_async(execution_id=execution_id)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scenario suite execution '{execution_id}' is no longer available",
        )
    return execution


@router.post(
    "/{name}/launch",
    response_model=ScenarioSuiteExecutionSummary,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ProblemDetail, "description": "The suite has unresolved or invalid entries"},
        404: {"model": ProblemDetail, "description": "Scenario suite not found"},
        409: {"model": ProblemDetail, "description": "The suite or a referenced preset changed"},
        429: {"model": ProblemDetail, "description": "Active suite execution capacity is full"},
        503: {"model": ProblemDetail, "description": "Runtime or definition storage unavailable"},
    },
)
async def launch_scenario_suite(  # pyrit-async-suffix-exempt
    name: str,
    body: LaunchScenarioSuiteRequest,
    http_request: Request,
) -> ScenarioSuiteExecutionSummary:
    """
    Accept one transient suite execution after registry-backed preflight.

    Returns:
        ScenarioSuiteExecutionSummary: Server-owned execution identity and
            initial ordered entry states.
    """
    runtime = getattr(http_request.app.state, "runtime_lifecycle", None)
    if runtime is not None and runtime.state != "ready":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scenario suite launch was interrupted by runtime reinitialization.",
        )
    try:
        return await get_scenario_suite_execution_service().launch_async(name=name, request=body)
    except ScenarioSuiteExecutionNotFoundError as exc:
        raise _not_found(name) from exc
    except ScenarioSuiteLaunchConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ScenarioSuiteConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenario suite '{name}' changed after it was reviewed; refresh it before launching.",
        ) from exc
    except ScenarioSuiteNotRunnableError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except ScenarioSuiteExecutionCapacityError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from None
    except AzureError as exc:
        raise _storage_unavailable() from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None


@router.get(
    "/{name}",
    response_model=ScenarioSuiteResponse,
    responses={404: {"model": ProblemDetail, "description": "Scenario suite not found"}},
)
async def get_scenario_suite(name: str) -> ScenarioSuiteResponse:  # pyrit-async-suffix-exempt
    """
    Get one scenario suite and the version required to update it.

    Returns:
        ScenarioSuiteResponse: The suite and current entry statuses.
    """
    return await _load_suite_or_404_async(name)


@router.post(
    "",
    response_model=ScenarioSuiteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    responses={
        409: {
            "model": ProblemDetail,
            "description": "A suite is already stored under this name, or the name is registered in code",
        }
    },
)
async def create_scenario_suite(  # pyrit-async-suffix-exempt
    suite: ScenarioSuite,
    author: Annotated[str | None, Depends(current_user_name)],
) -> ScenarioSuiteResponse:
    """
    Create a scenario suite that must not already exist.

    Returns:
        ScenarioSuiteResponse: The newly stored suite and current entry statuses.
    """
    if suite.author is None and author is not None:
        suite = suite.model_copy(update={"author": author})

    try:
        return await get_scenario_suite_service().save_suite_async(suite=suite, expected_version=None)
    except ScenarioSuiteNameRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenario suite '{suite.name}' is registered in code and cannot be stored",
        ) from exc
    except ScenarioSuiteConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenario suite '{suite.name}' already exists",
        ) from exc
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.put(
    "/{name}",
    response_model=ScenarioSuiteResponse,
    dependencies=[Depends(require_admin)],
    responses={
        400: {"model": ProblemDetail, "description": "Body name does not match the path"},
        409: {
            "model": ProblemDetail,
            "description": "The suite changed after it was read, or the name is registered in code",
        },
    },
)
async def update_scenario_suite(  # pyrit-async-suffix-exempt
    name: str,
    body: UpdateScenarioSuiteRequest,
) -> ScenarioSuiteResponse:
    """
    Replace a scenario suite the caller has read.

    Returns:
        ScenarioSuiteResponse: The updated suite and current entry statuses.
    """
    if body.suite.name != name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Scenario suite name '{body.suite.name}' does not match path '{name}'",
        )

    try:
        service = get_scenario_suite_service()
        suite = body.suite
        if suite.author is None:
            existing = await service.load_suite_async(name=name)
            if existing is not None:
                suite = suite.model_copy(update={"author": existing.suite.author})
        return await service.save_suite_async(suite=suite, expected_version=body.expected_version)
    except ScenarioSuiteNameRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenario suite '{name}' is registered in code and cannot be stored",
        ) from exc
    except ScenarioSuiteConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenario suite '{name}' changed since it was read; re-read it and retry",
        ) from exc
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    responses={404: {"model": ProblemDetail, "description": "Scenario suite not found"}},
)
async def delete_scenario_suite(name: str) -> None:  # pyrit-async-suffix-exempt
    """Delete one stored scenario suite."""
    try:
        deleted = await get_scenario_suite_service().delete_suite_async(name=name)
    except AzureError as exc:
        raise _storage_unavailable() from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    if not deleted:
        raise _not_found(name)
