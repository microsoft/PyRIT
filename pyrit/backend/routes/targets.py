# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target API routes.

Provides endpoints for managing target instances.
Target types are set at app startup via initializers - you cannot add new types at runtime.
"""

from fastapi import APIRouter, HTTPException, Query, Request, status

from pyrit.backend.middleware.auth import has_admin_access
from pyrit.backend.models.common import CursorStr, IdentifierStr, ProblemDetail
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    DetectTargetCapabilitiesRequest,
    TargetListResponse,
    TargetTypeResponse,
    UnregisteredTarget,
)
from pyrit.backend.services.target_service import get_target_service
from pyrit.models.catalog.target import TargetInstance
from pyrit.prompt_target.common.discover_target_capabilities import CapabilityDiscoveryError

router = APIRouter(prefix="/targets", tags=["targets"])


@router.post(
    "/{target_registry_name}/detect-capabilities",
    response_model=TargetInstance,
    responses={
        400: {"model": ProblemDetail, "description": "Target cannot be probed safely"},
        404: {"model": ProblemDetail, "description": "Target not found"},
        409: {"model": ProblemDetail, "description": "Target changed or detection is already running"},
        502: {"model": ProblemDetail, "description": "Capability detection was inconclusive"},
        504: {"model": ProblemDetail, "description": "Capability detection timed out"},
    },
)
async def detect_capabilities_async(
    target_registry_name: IdentifierStr, body: DetectTargetCapabilitiesRequest
) -> TargetInstance:
    """
    Run live checks and automatically update a registered target.

    Returns:
        TargetInstance: The updated live target.
    """
    try:
        return await get_target_service().detect_capabilities_async(
            target_registry_name=target_registry_name, expected_hash=body.expected_hash
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CapabilityDiscoveryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504, detail="Capability detection timed out; capabilities were not changed."
        ) from exc


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
    return await service.list_targets_async(limit=limit, cursor=cursor)


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

    Note: Sensitive parameters (API keys, tokens) are filtered from the response.

    Returns:
        TargetInstance | UnregisteredTarget: A named instance or an unregistered descriptor.
    """
    service = get_target_service()

    try:
        return await service.create_target_async(
            request=body,
            upload_directory=_target_upload_directory(request),
            is_admin=has_admin_access(request),
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
            detail=f"Target '{target_registry_name}' not found",
        )

    return target
