# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario scan definition API routes."""

from typing import Annotated

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Depends, HTTPException, status

from pyrit.backend.middleware.auth import current_user_name, require_admin
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scenario_scans import (
    ScenarioScanListResponse,
    ScenarioScanResponse,
    UpdateScenarioScanRequest,
)
from pyrit.backend.services.scenario_scan_service import get_scenario_scan_service
from pyrit.models.catalog import ScenarioScan
from pyrit.registry import ScenarioScanConflictError

router = APIRouter(prefix="/scenario-scans", tags=["scenario-scans"])


def _storage_unavailable() -> HTTPException:
    """
    Create a sanitized response for unavailable scan storage.

    Returns:
        HTTPException: A service-unavailable response without SDK details.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Scenario scan storage is temporarily unavailable",
    )


def _not_found(name: str) -> HTTPException:
    """
    Create a not-found response for a missing scan.

    Returns:
        HTTPException: A not-found response naming the scan.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scenario scan '{name}' not found")


async def _load_scan_or_404_async(name: str) -> ScenarioScanResponse:
    """
    Read one scan, translating storage failures into HTTP responses.

    Returns:
        ScenarioScanResponse: The stored scan and current entry statuses.

    Raises:
        HTTPException: 404 if absent, 400 for an illegal name, or 503 if storage fails.
    """
    try:
        scan = await get_scenario_scan_service().get_scan_async(name=name)
    except AzureError as exc:
        raise _storage_unavailable() from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    if scan is None:
        raise _not_found(name)
    return scan


@router.get("", response_model=ScenarioScanListResponse)
async def list_scenario_scans() -> ScenarioScanListResponse:  # pyrit-async-suffix-exempt
    """
    List every readable scenario scan from the configured storage source.

    Returns:
        ScenarioScanListResponse: Stored scans with current entry statuses.
    """
    try:
        return await get_scenario_scan_service().list_scans_async()
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.get(
    "/{name}",
    response_model=ScenarioScanResponse,
    responses={404: {"model": ProblemDetail, "description": "Scenario scan not found"}},
)
async def get_scenario_scan(name: str) -> ScenarioScanResponse:  # pyrit-async-suffix-exempt
    """
    Get one scenario scan and the version required to update it.

    Returns:
        ScenarioScanResponse: The stored scan and current entry statuses.
    """
    return await _load_scan_or_404_async(name)


@router.post(
    "",
    response_model=ScenarioScanResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    responses={409: {"model": ProblemDetail, "description": "A scan is already stored under this name"}},
)
async def create_scenario_scan(  # pyrit-async-suffix-exempt
    scan: ScenarioScan,
    author: Annotated[str | None, Depends(current_user_name)],
) -> ScenarioScanResponse:
    """
    Create a scenario scan that must not already exist.

    Returns:
        ScenarioScanResponse: The newly stored scan and current entry statuses.
    """
    if scan.author is None and author is not None:
        scan = scan.model_copy(update={"author": author})

    try:
        return await get_scenario_scan_service().save_scan_async(scan=scan, expected_version=None)
    except ScenarioScanConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenario scan '{scan.name}' already exists",
        ) from exc
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.put(
    "/{name}",
    response_model=ScenarioScanResponse,
    dependencies=[Depends(require_admin)],
    responses={
        400: {"model": ProblemDetail, "description": "Body name does not match the path"},
        409: {"model": ProblemDetail, "description": "The scan changed after it was read"},
    },
)
async def update_scenario_scan(  # pyrit-async-suffix-exempt
    name: str,
    body: UpdateScenarioScanRequest,
) -> ScenarioScanResponse:
    """
    Replace a scenario scan the caller has read.

    Returns:
        ScenarioScanResponse: The updated scan and current entry statuses.
    """
    if body.scan.name != name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Scenario scan name '{body.scan.name}' does not match path '{name}'",
        )

    try:
        service = get_scenario_scan_service()
        scan = body.scan
        if scan.author is None:
            stored = await service.get_scan_async(name=name)
            if stored is not None:
                scan = scan.model_copy(update={"author": stored.scan.author})
        return await service.save_scan_async(scan=scan, expected_version=body.expected_version)
    except ScenarioScanConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenario scan '{name}' changed since it was read; re-read it and retry",
        ) from exc
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    responses={404: {"model": ProblemDetail, "description": "Scenario scan not found"}},
)
async def delete_scenario_scan(name: str) -> None:  # pyrit-async-suffix-exempt
    """Delete one stored scenario scan."""
    try:
        deleted = await get_scenario_scan_service().delete_scan_async(name=name)
    except AzureError as exc:
        raise _storage_unavailable() from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    if not deleted:
        raise _not_found(name)
