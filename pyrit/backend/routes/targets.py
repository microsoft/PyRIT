# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target API routes.

Provides endpoints for managing target instances.
Target types are set at app startup via initializers - you cannot add new types at runtime.
"""

from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from pyrit.backend.middleware.auth import AuthenticatedUser, has_admin_access
from pyrit.backend.models import (
    CreateTargetRequest,
    ProblemDetail,
    TargetListResponse,
    UpdateTargetCredentialsRequest,
)
from pyrit.backend.models.common import CursorStr, IdentifierStr, NotSaveableProblemDetail, SaveabilityResponse
from pyrit.backend.models.targets import (
    DetectTargetCapabilitiesRequest,
    TargetSaveabilityRequest,
    TargetTypeResponse,
    UnregisteredTarget,
)
from pyrit.backend.services import get_target_service
from pyrit.backend.services.target_service import CredentialUpdateConflictError
from pyrit.models.catalog import SavedEntryOwner, TargetInstance
from pyrit.prompt_target.common.discover_target_capabilities import CapabilityDiscoveryError
from pyrit.registry import NotSaveableError, SavedEntryConflictError, SavedEntryStorageError
from pyrit.registry.instance_registry import RegistryNameTakenError

router = APIRouter(prefix="/targets", tags=["targets"])

# Problem type for a target that can't be saved as asked but can still be created without saving it.
NOT_SAVEABLE_PROBLEM_TYPE = "/errors/not-saveable"


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
    # RuntimeLifecycle sets this from the validated server config, always as str | None.
    return cast("str | None", getattr(request.app.state, "target_upload_directory", None))


def _saved_entry_owner(request: Request) -> SavedEntryOwner | None:
    """
    Return the signed-in user to record on a saved target.

    Args:
        request: The incoming FastAPI request.

    Returns:
        SavedEntryOwner | None: The user's object ID and name, or None when sign-in is off.
    """
    user = getattr(request.state, "user", None)
    if not isinstance(user, AuthenticatedUser):
        return None
    return SavedEntryOwner(oid=user.oid, name=user.name or user.email)


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
    "/saveability",
    response_model=SaveabilityResponse,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid target type or parameters"},
        403: {
            "model": ProblemDetail,
            "description": "Administrator access is required to create this target",
        },
    },
)
async def check_target_saveability(  # pyrit-async-suffix-exempt
    *,
    body: TargetSaveabilityRequest,
    request: Request,
) -> SaveabilityResponse:
    """
    Say whether a target could be saved as asked, without building or saving it.

    Only the type, parameters, and auth mode are checked, by create's parameter, permission, and save checks. The
    name and registration options aren't checked, and the target isn't built, so its constructor's own checks don't
    run: a null reason means the save checks pass, not that creating the target succeeds.

    Returns:
        SaveabilityResponse: Why the target can't be saved, or no reason when the save checks pass.
    """
    try:
        return await get_target_service().check_saveability_async(
            request=body,
            upload_directory=_target_upload_directory(request),
            is_admin=has_admin_access(request),
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post(
    "",
    response_model=TargetInstance | UnregisteredTarget,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {
            "model": NotSaveableProblemDetail,
            "description": (
                f"Invalid target type or parameters, or (type {NOT_SAVEABLE_PROBLEM_TYPE}) a target that can't be "
                "saved as asked but can be created without saving it"
            ),
        },
        403: {
            "model": ProblemDetail,
            "description": "Administrator access is required to run model repository code or to use the "
            "server's stored credentials, or a private target was requested without a signed-in caller",
        },
        409: {
            "model": ProblemDetail,
            "description": "A registered target has the name, or, for a target to save, a saved target has it",
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

    Note: Sensitive parameters (API keys, tokens) are filtered from the response. A
    target is shared unless ``private`` is true or it is built on the caller's private
    targets; supplying credentials doesn't make it private. A private target's credentials
    can be updated later by its creator, a shared target's only when
    ``allow_credential_updates`` is true.

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
            owner=_saved_entry_owner(request) if body.persist else None,
            recipes_source=getattr(request.app.state, "registry_recipes_source", None),
        )
    except NotSaveableError as e:
        problem = NotSaveableProblemDetail(
            type=NOT_SAVEABLE_PROBLEM_TYPE,
            title="Can't be saved",
            status=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
            not_saveable_reason=e.cause,
            instance=str(request.url.path),
        )
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=problem.model_dump(exclude_none=True))
    except (SavedEntryConflictError, RegistryNameTakenError) as e:
        # RegistryNameTakenError is a ValueError, and a taken name is a 409 whether or not the target is saved.
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


@router.put(
    "/{target_registry_name}/credentials",
    response_model=TargetInstance,
    responses={
        400: {
            "model": ProblemDetail,
            "description": "A value can't be sent, or the change would alter how the target authenticates",
        },
        403: {
            "model": ProblemDetail,
            "description": "No signed-in caller, or the target is saved or doesn't allow credential updates",
        },
        404: {"model": ProblemDetail, "description": "Target not found"},
        409: {"model": ProblemDetail, "description": "The target isn't private or shared as the request expects"},
    },
)
async def update_target_credentials(
    *,
    target_registry_name: IdentifierStr,
    body: UpdateTargetCredentialsRequest,
) -> TargetInstance:  # pyrit-async-suffix-exempt
    """
    Replace a target's API key or request headers in place.

    Works on a target created with credential updates allowed: your private target (private
    targets allow them by default), or a shared target that allows them, which anyone signed in
    can update. Everything using the target sends the new values with its next request; the
    target's identity, configuration, and authentication mode don't change. Values are never
    returned.

    Returns:
        TargetInstance: The updated target.
    """
    try:
        target = await get_target_service().update_credentials_async(
            target_registry_name=target_registry_name, request=body
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except CredentialUpdateConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target '{target_registry_name}' not found",
        )
    return target
