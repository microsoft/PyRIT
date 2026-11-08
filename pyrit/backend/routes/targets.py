# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target API routes.

Provides endpoints for managing target instances.
Target types are set at app startup via initializers - you cannot add new types at runtime.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from pyrit.backend.middleware.auth import current_user_oid, has_admin_access
from pyrit.backend.models import (
    CreateTargetRequest,
    ProblemDetail,
    TargetListResponse,
)
from pyrit.backend.models.common import CursorStr, IdentifierStr
from pyrit.backend.models.targets import (
    TargetTypeResponse,
    UnregisteredTarget,
)
from pyrit.backend.routes.common import (
    NOT_SAVEABLE_PROBLEM_TYPE,
    not_saveable_response,
    saved_entry_owner_from_request,
)
from pyrit.backend.services import get_target_service
from pyrit.backend.services.saved_entries import NotSaveableError, SavedEntryConflictError, SavedEntryStorageError
from pyrit.models.catalog import TargetInstance

router = APIRouter(prefix="/targets", tags=["targets"])


def _target_upload_directory(request: Request) -> str | None:
    """
    Return the folder the server lets API-created targets upload files from.

    Args:
        request: The incoming FastAPI request.

    Returns:
        str | None: The configured folder, or None when the server configures none.
    """
    directory: str | None = getattr(request.app.state, "target_upload_directory", None)
    return directory


@router.get(
    "",
    response_model=TargetListResponse,
    responses={
        500: {"model": ProblemDetail, "description": "Internal server error"},
    },
)
async def list_targets(  # pyrit-async-suffix-exempt
    user_oid: Annotated[str | None, Depends(current_user_oid)],
    limit: int = Query(50, ge=1, le=200, description="Maximum items per page"),
    cursor: CursorStr | None = Query(None, description="Pagination cursor (target_registry_name)"),
) -> TargetListResponse:
    """
    List target instances with pagination.

    Returns paginated target instances.

    Returns:
        TargetListResponse: Paginated list of target instances.
    """
    service = get_target_service()
    return await service.list_targets_async(limit=limit, cursor=cursor, current_user_oid=user_oid)


@router.get(
    "/types",
    response_model=TargetTypeResponse,
    responses={
        500: {"model": ProblemDetail, "description": "Internal server error"},
    },
)
async def list_target_types(request: Request) -> TargetTypeResponse:  # pyrit-async-suffix-exempt
    """
    List target types projected from ``TargetRegistry`` metadata.

    Returns:
        TargetTypeResponse: Available target types and build parameters.
    """
    service = get_target_service()
    return await service.list_target_types_async(upload_directory=_target_upload_directory(request))


@router.post(
    "",
    response_model=TargetInstance | UnregisteredTarget,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {
            "model": ProblemDetail,
            "description": (
                f"Invalid target type or parameters, or (type {NOT_SAVEABLE_PROBLEM_TYPE}) a target that can't be "
                "saved as asked but can be created without saving it"
            ),
        },
        403: {
            "model": ProblemDetail,
            "description": "Administrator access is required to run model repository code or to use the "
            "server's stored credentials",
        },
        409: {
            "model": ProblemDetail,
            "description": "A target to save has the name of a registered or saved target",
        },
        503: {"model": ProblemDetail, "description": "Saved targets can't be written right now"},
    },
)
async def create_target(
    *,
    body: CreateTargetRequest,
    request: Request,
) -> TargetInstance | UnregisteredTarget | JSONResponse:  # pyrit-async-suffix-exempt
    """
    Create a new target instance.

    Instantiates a target with the given type and parameters. Registration is
    optional; unregistered responses contain no reusable object handle.

    Note: Sensitive parameters (API keys, tokens) are filtered from the response.

    Returns:
        TargetInstance | UnregisteredTarget | JSONResponse: A named instance or an unregistered descriptor,
            or the problem response for a target that can't be saved as asked.
    """
    service = get_target_service()

    try:
        return await service.create_target_async(
            request=body,
            upload_directory=_target_upload_directory(request),
            is_admin=has_admin_access(request),
            owner=saved_entry_owner_from_request(request),
            recipes_source=getattr(request.app.state, "registry_recipes_source", None),
        )
    except NotSaveableError as e:
        return not_saveable_response(detail=str(e), request=request)
    except SavedEntryConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except SavedEntryStorageError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create target: {str(e)}",
        ) from e


@router.get(
    "/{target_registry_name}",
    response_model=TargetInstance,
    responses={
        404: {"model": ProblemDetail, "description": "Target not found"},
    },
)
async def get_target(
    target_registry_name: IdentifierStr,
    user_oid: Annotated[str | None, Depends(current_user_oid)],
) -> TargetInstance:  # pyrit-async-suffix-exempt
    """
    Get a target instance by registry name.

    Returns:
        TargetInstance: The target instance details.
    """
    service = get_target_service()

    target = await service.get_target_async(target_registry_name=target_registry_name, current_user_oid=user_oid)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target '{target_registry_name}' not found",
        )

    return target
