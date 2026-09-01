# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer API routes.

Provides endpoints for listing, registering, and removing initializers.

Route structure:
    GET    /api/initializers                — list all initializers
    GET    /api/initializers/settings       — list baseline + additional initializers
    POST   /api/initializers/settings       — add an additional initializer
    PUT    /api/initializers/settings/{id}  — update an additional initializer
    DELETE /api/initializers/settings/{id}  — remove an additional initializer
    POST   /api/initializers/{name}/apply   — apply an initializer immediately
    GET    /api/initializers/{name}         — get single initializer detail
    POST   /api/initializers                — register initializer from script
    DELETE /api/initializers/{name}         — unregister an initializer
"""

from azure.core.exceptions import AzureError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.initializers import (
    ApplyInitializerRequest,
    ApplyInitializerResponse,
    BaselineInitializerSetting,
    CreateAdditionalInitializerRequest,
    CustomInitializerListResponse,
    InitializerSettingsResponse,
    ListRegisteredInitializersResponse,
    RegisterInitializerRequest,
    UpdateAdditionalInitializerRequest,
)
from pyrit.backend.services.initializer_service import get_initializer_service
from pyrit.models import AdditionalInitializer
from pyrit.models.catalog.initializer import RegisteredInitializer

router = APIRouter(prefix="/initializers", tags=["initializers"])


def _custom_storage_unavailable() -> HTTPException:
    """
    Create a sanitized response for unavailable custom initializer storage.

    Returns:
        HTTPException: A service-unavailable response without SDK details.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Custom initializer storage is temporarily unavailable",
    )


def _baseline_initializers(request: Request) -> list[BaselineInitializerSetting]:
    """
    Read the read-only baseline initializer list captured at backend startup.

    The startup lifespan (see ``pyrit.backend.main.lifespan``) stashes the ``.pyrit_conf``
    baseline on ``app.state`` so routes can display it without importing the configuration
    loader. Falls back to an empty list when the app was not started via the lifespan
    (e.g. isolated route tests).

    Args:
        request: The incoming FastAPI request.

    Returns:
        list[BaselineInitializerSetting]: The baseline initializers, or an empty list.
    """
    return list(getattr(request.app.state, "baseline_initializers", []))


def _check_custom_initializers_allowed(request: Request) -> None:
    """
    Check that allow_custom_initializers is enabled on the server.

    Args:
        request: The incoming FastAPI request.

    Raises:
        HTTPException: 403 if custom initializer operations are not enabled.
    """
    allowed = getattr(request.app.state, "allow_custom_initializers", False)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Custom initializer operations are disabled. "
                "Set allow_custom_initializers: true in .pyrit_conf to enable."
            ),
        )


@router.get(
    "",
    response_model=ListRegisteredInitializersResponse,
)
async def list_initializers(  # pyrit-async-suffix-exempt
    limit: int = Query(50, ge=1, le=200, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Pagination cursor (initializer_name to start after)"),
) -> ListRegisteredInitializersResponse:
    """
    List all available initializers.

    Returns initializer metadata including required environment variables,
    supported parameters, and descriptions.

    Returns:
        ListRegisteredInitializersResponse: Paginated list of initializer summaries.
    """
    service = get_initializer_service()
    return await service.list_initializers_async(limit=limit, cursor=cursor)


@router.get(
    "/settings",
    response_model=InitializerSettingsResponse,
)
async def get_initializer_settings(  # pyrit-async-suffix-exempt
    request: Request,
) -> InitializerSettingsResponse:
    """
    List the read-only ``.pyrit_conf`` baseline plus the persisted additional initializers.

    Args:
        request: The incoming FastAPI request (carries the startup baseline on ``app.state``).

    Returns:
        InitializerSettingsResponse: The read-only baseline and editable additional lists.
    """
    service = get_initializer_service()
    return await service.list_initializer_settings_async(
        baseline_initializers=_baseline_initializers(request),
    )


@router.post(
    "/settings",
    response_model=AdditionalInitializer,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    responses={
        400: {"model": ProblemDetail, "description": "Invalid initializer settings"},
        404: {"model": ProblemDetail, "description": "Initializer not found"},
    },
)
async def create_additional_initializer(  # pyrit-async-suffix-exempt
    body: CreateAdditionalInitializerRequest,
) -> AdditionalInitializer:
    """
    Add a new additional initializer.

    Args:
        body: The additional initializer to add.

    Returns:
        AdditionalInitializer: The newly persisted row.
    """
    service = get_initializer_service()
    try:
        return await service.create_additional_initializer_async(
            initializer_name=body.initializer_name,
            parameters=body.parameters,
            order_index=body.order_index,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Initializer '{body.initializer_name}' not found",
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.put(
    "/settings/{additional_initializer_id}",
    response_model=AdditionalInitializer,
    dependencies=[Depends(require_admin)],
    responses={
        400: {"model": ProblemDetail, "description": "Invalid initializer settings"},
        404: {"model": ProblemDetail, "description": "Additional initializer not found"},
    },
)
async def update_additional_initializer(  # pyrit-async-suffix-exempt
    additional_initializer_id: str,
    body: UpdateAdditionalInitializerRequest,
) -> AdditionalInitializer:
    """
    Update one existing additional initializer by id.

    Args:
        additional_initializer_id: The additional initializer row id to update.
        body: The updated payload.

    Returns:
        AdditionalInitializer: The updated row.
    """
    service = get_initializer_service()
    try:
        return await service.update_additional_initializer_async(
            initializer_id=additional_initializer_id,
            parameters=body.parameters,
            order_index=body.order_index,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Additional initializer '{additional_initializer_id}' not found",
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.delete(
    "/settings/{additional_initializer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_additional_initializer(  # pyrit-async-suffix-exempt
    additional_initializer_id: str,
) -> None:
    """
    Delete one additional initializer by id.

    Args:
        additional_initializer_id: The additional initializer row id to delete.
    """
    service = get_initializer_service()
    await service.delete_additional_initializer_async(initializer_id=additional_initializer_id)


@router.post(
    "/{initializer_name}/apply",
    response_model=ApplyInitializerResponse,
    dependencies=[Depends(require_admin)],
    responses={
        400: {"model": ProblemDetail, "description": "Initializer apply failed"},
        404: {"model": ProblemDetail, "description": "Initializer not found"},
    },
)
async def apply_initializer(  # pyrit-async-suffix-exempt
    initializer_name: str,
    body: ApplyInitializerRequest | None = None,
) -> ApplyInitializerResponse:
    """
    Apply one initializer immediately without saving it.

    Args:
        initializer_name: Registry name of the initializer to apply.
        body: Optional one-time parameters for this execution.

    Returns:
        ApplyInitializerResponse: Success metadata for the apply-now operation.
    """
    service = get_initializer_service()
    try:
        return await service.apply_initializer_async(
            initializer_name=initializer_name,
            parameters=body.parameters if body else None,
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Initializer '{initializer_name}' not found",
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get(
    "/custom",
    response_model=CustomInitializerListResponse,
    dependencies=[Depends(require_admin)],
)
async def list_custom_initializers(request: Request) -> CustomInitializerListResponse:  # pyrit-async-suffix-exempt
    """
    List custom initializer scripts from the configured storage source.

    Returns:
        CustomInitializerListResponse: The configured source and stored scripts.
    """
    _check_custom_initializers_allowed(request)
    try:
        return await get_initializer_service().list_custom_initializers_async()
    except AzureError as exc:
        raise _custom_storage_unavailable() from exc


@router.get(
    "/{initializer_name}",
    response_model=RegisteredInitializer,
    responses={
        404: {"model": ProblemDetail, "description": "Initializer not found"},
    },
)
async def get_initializer(initializer_name: str) -> RegisteredInitializer:  # pyrit-async-suffix-exempt
    """
    Get details for a specific initializer.

    Args:
        initializer_name: Registry name of the initializer (e.g., 'target').

    Returns:
        RegisteredInitializer: Full initializer metadata.
    """
    service = get_initializer_service()

    initializer = await service.get_initializer_async(initializer_name=initializer_name)
    if not initializer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Initializer '{initializer_name}' not found",
        )

    return initializer


@router.post(
    "",
    response_model=RegisteredInitializer,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    responses={
        403: {"model": ProblemDetail, "description": "Custom initializer operations disabled"},
        409: {"model": ProblemDetail, "description": "Initializer name already registered"},
    },
)
async def register_initializer(  # pyrit-async-suffix-exempt
    request: Request,
    body: RegisterInitializerRequest,
) -> RegisteredInitializer:
    """
    Register an initializer by uploading Python source code.

    The script must contain a concrete PyRITInitializer subclass.
    Requires allow_custom_initializers to be enabled in pyrit_conf.

    Args:
        request: The incoming FastAPI request.
        body: Request body with name and script_content.

    Returns:
        The newly registered initializer summary.
    """
    _check_custom_initializers_allowed(request)
    service = get_initializer_service()

    try:
        return await service.register_initializer_async(name=body.name, script_content=body.script_content)
    except AzureError as exc:
        raise _custom_storage_unavailable() from exc
    except ValueError as e:
        detail = str(e)
        if "already registered" in detail:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from None
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from None


@router.delete(
    "/{initializer_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    responses={
        400: {"model": ProblemDetail, "description": "Cannot remove built-in initializer"},
        403: {"model": ProblemDetail, "description": "Custom initializer operations disabled"},
        404: {"model": ProblemDetail, "description": "Initializer not found"},
    },
)
async def unregister_initializer(  # pyrit-async-suffix-exempt
    request: Request,
    initializer_name: str,
) -> None:
    """
    Remove a custom initializer from the registry.

    Built-in initializers cannot be removed. Requires
    allow_custom_initializers to be enabled in pyrit_conf.

    Args:
        request: The incoming FastAPI request.
        initializer_name: Registry name of the initializer to remove.
    """
    _check_custom_initializers_allowed(request)
    service = get_initializer_service()

    try:
        await service.unregister_initializer_async(initializer_name=initializer_name)
    except AzureError as exc:
        raise _custom_storage_unavailable() from exc
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from None
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Initializer '{initializer_name}' not found",
        ) from None
