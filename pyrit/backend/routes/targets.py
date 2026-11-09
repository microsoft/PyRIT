# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target API routes.

Provides endpoints for managing target instances.
Target types are set at app startup via initializers - you cannot add new types at runtime.
"""

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from pyrit.backend.middleware.auth import caller_principal, has_admin_access
from pyrit.backend.models import (
    CreateTargetRequest,
    ProblemDetail,
    SetTargetCredentialRequest,
    TargetListResponse,
)
from pyrit.backend.models.common import CursorStr, IdentifierStr
from pyrit.backend.models.targets import (
    TargetTypeResponse,
    UnregisteredTarget,
)
from pyrit.backend.services import get_target_service
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


def _require_single_process(request: Request) -> None:
    """
    Reject caller credentials when requests may reach a backend process that does not hold them.

    Raises:
        HTTPException: 409 when the deployment runs more than one backend worker or replica.
    """
    runtime = getattr(request.app.state, "runtime_lifecycle", None)
    if runtime is not None and not runtime.topology_supported:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Caller credentials are kept in one backend process and require one backend worker and one replica.",
        )


def _target_not_found(target_registry_name: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Target '{target_registry_name}' not found",
    )


@router.get(
    "",
    response_model=TargetListResponse,
    responses={
        500: {"model": ProblemDetail, "description": "Internal server error"},
    },
)
async def list_targets(  # pyrit-async-suffix-exempt
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="Maximum items per page"),
    cursor: CursorStr | None = Query(None, description="Pagination cursor (target_registry_name)"),
) -> TargetListResponse:
    """
    List target instances with pagination.

    Returns paginated target instances, including whether the caller has set their own
    credential for targets that require one.

    Returns:
        TargetListResponse: Paginated list of target instances.
    """
    service = get_target_service()
    return await service.list_targets_async(limit=limit, cursor=cursor, caller=caller_principal(request))


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
            "description": "Invalid target type or parameters",
        },
        403: {
            "model": ProblemDetail,
            "description": "Administrator access is required to run model repository code or to use the "
            "server's stored credentials",
        },
    },
)
async def create_target(
    *,
    body: CreateTargetRequest,
    request: Request,
) -> TargetInstance | UnregisteredTarget:  # pyrit-async-suffix-exempt
    """
    Create a new target instance.

    Instantiates a target with the given type and parameters. Registration is
    optional; unregistered responses contain no reusable object handle.

    Note: Sensitive parameters (API keys, tokens) are filtered from the response. With
    ``requires_caller_credential``, a supported target keeps no submitted ``api_key``: it is
    stored only for this caller, and other callers must set their own credential to use it.

    Returns:
        TargetInstance | UnregisteredTarget: A named instance or an unregistered descriptor.
    """
    service = get_target_service()

    try:
        return await service.create_target_async(
            request=body,
            upload_directory=_target_upload_directory(request),
            is_admin=has_admin_access(request),
            caller=caller_principal(request),
        )
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
    request: Request,
) -> TargetInstance:  # pyrit-async-suffix-exempt
    """
    Get a target instance by registry name.

    Returns:
        TargetInstance: The target instance details.
    """
    service = get_target_service()

    target = await service.get_target_async(target_registry_name=target_registry_name, caller=caller_principal(request))
    if not target:
        raise _target_not_found(target_registry_name)

    return target


@router.delete(
    "/{target_registry_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        403: {"model": ProblemDetail, "description": "Only the caller who created the target can delete it"},
        404: {"model": ProblemDetail, "description": "Target not found"},
        409: {"model": ProblemDetail, "description": "Runtime work is active"},
    },
)
async def delete_target(
    target_registry_name: IdentifierStr,
    request: Request,
) -> Response:  # pyrit-async-suffix-exempt
    """
    Delete a target the caller created through the API.

    Deletion requires an idle runtime. Target resources are closed before every caller's
    credential is forgotten. Upstream credentials are not revoked.

    Returns:
        Response: An empty 204 response.
    """
    deleted = await get_target_service().delete_target_async(
        target_registry_name=target_registry_name, caller=caller_principal(request)
    )
    if not deleted:
        raise _target_not_found(target_registry_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{target_registry_name}/credentials",
    response_model=TargetInstance,
    responses={
        400: {"model": ProblemDetail, "description": "The target does not accept caller credentials"},
        403: {"model": ProblemDetail, "description": "No verified caller identity"},
        404: {"model": ProblemDetail, "description": "Target not found"},
        409: {"model": ProblemDetail, "description": "The deployment runs more than one backend process"},
    },
)
async def set_target_credential(
    target_registry_name: IdentifierStr,
    body: SetTargetCredentialRequest,
    request: Request,
) -> TargetInstance:  # pyrit-async-suffix-exempt
    """
    Set or replace the caller's own credential for a target.

    The credential is kept only in this backend process's memory and is never returned. It
    applies to the caller's next request through the target, including running work.

    Returns:
        TargetInstance: The target as seen by the caller.
    """
    _require_single_process(request)
    target = await get_target_service().set_caller_credential_async(
        target_registry_name=target_registry_name,
        caller=caller_principal(request),
        credential=body.credential.get_secret_value(),
    )
    if target is None:
        raise _target_not_found(target_registry_name)
    return target


@router.delete(
    "/{target_registry_name}/credentials",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ProblemDetail, "description": "The target does not accept caller credentials"},
        403: {"model": ProblemDetail, "description": "No verified caller identity"},
        404: {"model": ProblemDetail, "description": "Target not found"},
    },
)
async def delete_target_credential(
    target_registry_name: IdentifierStr,
    request: Request,
) -> Response:  # pyrit-async-suffix-exempt
    """
    Remove the caller's own credential for a target.

    Returns:
        Response: An empty 204 response.
    """
    removed = await get_target_service().remove_caller_credential_async(
        target_registry_name=target_registry_name, caller=caller_principal(request)
    )
    if not removed:
        raise _target_not_found(target_registry_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
