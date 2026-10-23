# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario suite definition API routes."""

from typing import Annotated

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Depends, HTTPException, status

from pyrit.backend.middleware.auth import current_user_name, require_admin
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scenario_suites import (
    ScenarioSuiteListResponse,
    ScenarioSuiteResponse,
    UpdateScenarioSuiteRequest,
)
from pyrit.backend.services.scenario_suite_service import get_scenario_suite_service
from pyrit.models.catalog import ScenarioSuite
from pyrit.registry import ScenarioSuiteConflictError

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
        ScenarioSuiteResponse: The stored suite and current entry statuses.

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
    List every readable scenario suite from the configured storage source.

    Returns:
        ScenarioSuiteListResponse: Stored suites with current entry statuses.
    """
    try:
        return await get_scenario_suite_service().list_suites_async()
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.get(
    "/{name}",
    response_model=ScenarioSuiteResponse,
    responses={404: {"model": ProblemDetail, "description": "Scenario suite not found"}},
)
async def get_scenario_suite(name: str) -> ScenarioSuiteResponse:  # pyrit-async-suffix-exempt
    """
    Get one scenario suite and the version required to update it.

    Returns:
        ScenarioSuiteResponse: The stored suite and current entry statuses.
    """
    return await _load_suite_or_404_async(name)


@router.post(
    "",
    response_model=ScenarioSuiteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    responses={409: {"model": ProblemDetail, "description": "A suite is already stored under this name"}},
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
        409: {"model": ProblemDetail, "description": "The suite changed after it was read"},
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
            stored = await service.load_suite_async(name=name)
            if stored is not None:
                suite = suite.model_copy(update={"author": stored.suite.author})
        return await service.save_suite_async(suite=suite, expected_version=body.expected_version)
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
