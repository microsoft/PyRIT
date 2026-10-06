# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target API routes.

Provides endpoints for managing target instances.
Target types are set at app startup via initializers - you cannot add new types at runtime.
Targets created here are saved and restored when the backend restarts.
"""

from fastapi import APIRouter, HTTPException, Query, Request, status

from pyrit.backend.middleware.auth import has_admin_access
from pyrit.backend.models.common import CursorStr, IdentifierStr, ProblemDetail
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    TargetListResponse,
    TargetTypeResponse,
    UpdateTargetRequest,
)
from pyrit.backend.routes.common import SAVED_INSTANCE_ERROR_RESPONSES, translate_saved_instance_errors
from pyrit.backend.services.target_service import get_target_service
from pyrit.models.catalog.target import TargetInstance

router = APIRouter(prefix="/targets", tags=["targets"])


@router.get(
    "",
    response_model=TargetListResponse,
    responses={
        500: {"model": ProblemDetail, "description": "Internal server error"},
    },
)
async def list_targets(  # pyrit-async-suffix-exempt
    limit: int = Query(50, ge=1, le=200, description="Maximum items per page"),
    cursor: CursorStr | None = Query(None, description="Pagination cursor (target_registry_name)"),
) -> TargetListResponse:
    """
    List target instances with pagination.

    Returns paginated target instances, and the saved targets that could not be restored.

    Returns:
        TargetListResponse: Paginated list of target instances.
    """
    service = get_target_service()
    return await service.list_targets_async(limit=limit, cursor=cursor)


@router.get(
    "/types",
    response_model=TargetTypeResponse,
    responses={
        500: {"model": ProblemDetail, "description": "Internal server error"},
    },
)
async def list_target_types() -> TargetTypeResponse:  # pyrit-async-suffix-exempt
    """
    List target types projected from ``TargetRegistry`` metadata.

    Returns:
        TargetTypeResponse: Available target types and build parameters.
    """
    service = get_target_service()
    return await service.list_target_types_async()


@router.post(
    "",
    response_model=TargetInstance,
    status_code=status.HTTP_201_CREATED,
    responses=SAVED_INSTANCE_ERROR_RESPONSES,
)
async def create_target(
    body: CreateTargetRequest,
    request: Request,
) -> TargetInstance:  # pyrit-async-suffix-exempt
    """
    Create and save a new target instance.

    Instantiates a target with the given type and parameters and saves it, so it is
    restored when the backend restarts. The target becomes available for use in attacks.
    Credential parameters are not saved as values: send them as references to server
    environment variables.

    Note: Sensitive parameters (API keys, tokens) are filtered from the response.

    Returns:
        TargetInstance: The created target instance details and saved version.
    """
    service = get_target_service()
    with translate_saved_instance_errors(action="create target"):
        return await service.create_target_async(request=body, is_admin=has_admin_access(request))


@router.get(
    "/{target_registry_name}",
    response_model=TargetInstance,
    responses={
        404: {"model": ProblemDetail, "description": "Target not found, or saved but not restored"},
    },
)
async def get_target(
    target_registry_name: IdentifierStr,
) -> TargetInstance:  # pyrit-async-suffix-exempt
    """
    Get a target instance by registry name.

    Returns:
        TargetInstance: The target instance details.
    """
    service = get_target_service()

    target = await service.get_target_async(target_registry_name=target_registry_name)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=service.describe_missing_target(target_registry_name=target_registry_name),
        )

    return target


@router.put(
    "/{target_registry_name}",
    response_model=TargetInstance,
    responses={
        **SAVED_INSTANCE_ERROR_RESPONSES,
        404: {"model": ProblemDetail, "description": "No saved target has this name"},
    },
)
async def update_target(
    target_registry_name: IdentifierStr,
    body: UpdateTargetRequest,
    request: Request,
) -> TargetInstance:  # pyrit-async-suffix-exempt
    """
    Replace a saved target.

    The request replaces the whole configuration: parameters and credentials it omits are removed.

    Returns:
        TargetInstance: The replacement target and its new saved version.
    """
    service = get_target_service()
    with translate_saved_instance_errors(action="update target"):
        return await service.update_target_async(
            target_registry_name=target_registry_name, request=body, is_admin=has_admin_access(request)
        )


@router.delete(
    "/{target_registry_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **SAVED_INSTANCE_ERROR_RESPONSES,
        404: {"model": ProblemDetail, "description": "Target not found"},
        428: {"model": ProblemDetail, "description": "Version required to delete a saved target"},
    },
)
async def delete_target(
    target_registry_name: IdentifierStr,
    request: Request,
    version: IdentifierStr | None = Query(
        None, description="Version returned when the target was read; required for saved targets"
    ),
) -> None:  # pyrit-async-suffix-exempt
    """Delete a saved target and unregister the target built from it."""
    service = get_target_service()
    with translate_saved_instance_errors(action="delete target"):
        deleted = await service.delete_target_async(
            target_registry_name=target_registry_name, expected_version=version, is_admin=has_admin_access(request)
        )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target '{target_registry_name}' not found",
        )
