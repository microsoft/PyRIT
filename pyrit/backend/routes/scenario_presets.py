# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario preset API routes.

Presets are data, not code, so unlike custom initializers they are not gated behind
an ``allow_`` switch. Reads are open and mutations require an admin, matching how
the rest of the API treats shared, server-side configuration.

Route structure:
    GET    /api/scenario-presets                  — list every stored preset
    GET    /api/scenario-presets/{name}           — get one preset and its version
    POST   /api/scenario-presets                  — create a preset that must not exist
    PUT    /api/scenario-presets/{name}           — replace a preset the caller has read
    DELETE /api/scenario-presets/{name}           — delete a preset
    POST   /api/scenario-presets/{name}/resolve   — combine a preset with launch fields
"""

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Depends, HTTPException, status

from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scenario_presets import (
    ResolveScenarioPresetRequest,
    ScenarioPresetListResponse,
    ScenarioPresetResponse,
    UpdateScenarioPresetRequest,
)
from pyrit.backend.services.scenario_preset_service import (
    ScenarioPresetNotFoundError,
    get_scenario_preset_service,
)
from pyrit.models.catalog import RunScenarioRequest, ScenarioPreset
from pyrit.registry import ScenarioPresetConflictError

router = APIRouter(prefix="/scenario-presets", tags=["scenario-presets"])


def _storage_unavailable() -> HTTPException:
    """
    Create a sanitized response for unavailable preset storage.

    Returns:
        HTTPException: A service-unavailable response without SDK details.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Scenario preset storage is temporarily unavailable",
    )


def _not_found(name: str) -> HTTPException:
    """
    Create a not-found response for a missing preset.

    Args:
        name: The requested preset name.

    Returns:
        HTTPException: A not-found response naming the preset.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Scenario preset '{name}' not found")


async def _load_preset_or_404_async(name: str) -> ScenarioPresetResponse:
    """
    Read one preset, translating storage failures into HTTP responses.

    Args:
        name: The preset name.

    Returns:
        ScenarioPresetResponse: The stored preset, its version, and its advisory issues.

    Raises:
        HTTPException: 404 if no preset is stored, 400 for an illegal name, 503 if storage fails.
    """
    try:
        preset = await get_scenario_preset_service().get_preset_async(name=name)
    except AzureError as exc:
        raise _storage_unavailable() from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    if preset is None:
        raise _not_found(name)
    return preset


@router.get("", response_model=ScenarioPresetListResponse)
async def list_scenario_presets() -> ScenarioPresetListResponse:  # pyrit-async-suffix-exempt
    """
    List every readable preset from the configured storage source.

    Presets that cannot be parsed are skipped rather than failing the listing.

    Returns:
        ScenarioPresetListResponse: The configured source and the stored presets.
    """
    try:
        return await get_scenario_preset_service().list_presets_async()
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.get(
    "/{name}",
    response_model=ScenarioPresetResponse,
    responses={404: {"model": ProblemDetail, "description": "Preset not found"}},
)
async def get_scenario_preset(name: str) -> ScenarioPresetResponse:  # pyrit-async-suffix-exempt
    """
    Get one preset and the version required to update it.

    Args:
        name: The preset name.

    Returns:
        ScenarioPresetResponse: The stored preset, its version, and its advisory issues.
    """
    return await _load_preset_or_404_async(name)


@router.post(
    "",
    response_model=ScenarioPresetResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    responses={
        409: {"model": ProblemDetail, "description": "A preset is already stored under this name"},
    },
)
async def create_scenario_preset(preset: ScenarioPreset) -> ScenarioPresetResponse:  # pyrit-async-suffix-exempt
    """
    Create a preset that must not already exist.

    References that do not resolve in this deployment are reported on the response
    rather than rejected, so a preset authored elsewhere can still be stored here.

    Args:
        preset: The preset to create.

    Returns:
        ScenarioPresetResponse: The persisted preset, its version, and its advisory issues.
    """
    try:
        return await get_scenario_preset_service().save_preset_async(preset=preset, expected_version=None)
    except ScenarioPresetConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenario preset '{preset.name}' already exists",
        ) from exc
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.put(
    "/{name}",
    response_model=ScenarioPresetResponse,
    dependencies=[Depends(require_admin)],
    responses={
        400: {"model": ProblemDetail, "description": "Body name does not match the path"},
        409: {"model": ProblemDetail, "description": "The preset changed after it was read"},
    },
)
async def update_scenario_preset(  # pyrit-async-suffix-exempt
    name: str,
    body: UpdateScenarioPresetRequest,
) -> ScenarioPresetResponse:
    """
    Replace a preset the caller has read.

    The update fails if the stored document no longer matches ``expected_version``,
    so a concurrent edit is reported instead of being silently overwritten.

    Args:
        name: The preset name from the path, which is authoritative.
        body: The replacement preset and the version it is replacing.

    Returns:
        ScenarioPresetResponse: The persisted preset, its new version, and its advisory issues.

    Raises:
        HTTPException: 400 if the body names a different preset, 409 if the stored version moved.
    """
    if body.preset.name != name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Preset name '{body.preset.name}' does not match path '{name}'",
        )

    try:
        return await get_scenario_preset_service().save_preset_async(
            preset=body.preset, expected_version=body.expected_version
        )
    except ScenarioPresetConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scenario preset '{name}' changed since it was read; re-read it and retry",
        ) from exc
    except AzureError as exc:
        raise _storage_unavailable() from exc


@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    responses={404: {"model": ProblemDetail, "description": "Preset not found"}},
)
async def delete_scenario_preset(name: str) -> None:  # pyrit-async-suffix-exempt
    """
    Delete one stored preset.

    Args:
        name: The preset name.

    Raises:
        HTTPException: 404 if no preset is stored, 400 for an illegal name, 503 if storage fails.
    """
    try:
        await get_scenario_preset_service().delete_preset_async(name=name)
    except ScenarioPresetNotFoundError:
        raise _not_found(name) from None
    except AzureError as exc:
        raise _storage_unavailable() from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.post(
    "/{name}/resolve",
    response_model=RunScenarioRequest,
    responses={404: {"model": ProblemDetail, "description": "Preset not found"}},
)
async def resolve_scenario_preset(  # pyrit-async-suffix-exempt
    name: str,
    body: ResolveScenarioPresetRequest,
) -> RunScenarioRequest:
    """
    Combine a stored preset with the launch-owned fields it omits.

    The result is an ordinary run request for the existing ``POST /scenarios/runs``
    endpoint. Resolving here rather than in each client keeps one implementation of
    the merge, so the distinction between "unset" and "set to the default" cannot
    drift between callers.

    Args:
        name: The preset name.
        body: The target and execution fields for this launch.

    Returns:
        RunScenarioRequest: The request to post to the scenario run endpoint.
    """
    stored = await _load_preset_or_404_async(name)
    return get_scenario_preset_service().resolve_run_request(preset=stored.preset, launch=body)
