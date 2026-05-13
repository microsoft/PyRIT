# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer API routes.

Provides endpoints for listing, registering, and removing initializers.

Route structure:
    GET    /api/initializers                — list all initializers
    GET    /api/initializers/{name}         — get single initializer detail
    POST   /api/initializers                — register initializer from script
    DELETE /api/initializers/{name}         — unregister an initializer
"""

from fastapi import APIRouter, HTTPException, Query, Request, status

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.initializers import (
    ListRegisteredInitializersResponse,
    RegisteredInitializer,
    RegisterInitializerRequest,
)
from pyrit.backend.services.initializer_service import get_initializer_service

router = APIRouter(prefix="/initializers", tags=["initializers"])


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
async def list_initializers(
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
    "/{initializer_name}",
    response_model=RegisteredInitializer,
    responses={
        404: {"model": ProblemDetail, "description": "Initializer not found"},
    },
)
async def get_initializer(initializer_name: str) -> RegisteredInitializer:
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
    response_model=list[RegisteredInitializer],
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"model": ProblemDetail, "description": "Custom initializer operations disabled"},
    },
)
async def register_initializer(
    request: Request,
    body: RegisterInitializerRequest,
) -> list[RegisteredInitializer]:
    """
    Register initializer(s) from a Python script on the server.

    Loads the script, discovers PyRITInitializer subclasses, and registers
    them in the initializer registry. Requires allow_custom_initializers
    to be enabled in pyrit_conf.

    Args:
        request: The incoming FastAPI request.
        body: Request body with script_path and optional name.

    Returns:
        List of newly registered initializer summaries.
    """
    _check_custom_initializers_allowed(request)
    service = get_initializer_service()

    try:
        return await service.register_initializer_async(script_path=body.script_path, name=body.name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None


@router.delete(
    "/{initializer_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        403: {"model": ProblemDetail, "description": "Custom initializer operations disabled"},
        404: {"model": ProblemDetail, "description": "Initializer not found"},
    },
)
async def unregister_initializer(
    request: Request,
    initializer_name: str,
) -> None:
    """
    Remove an initializer from the registry.

    Any initializer (built-in or custom) can be removed. Requires
    allow_custom_initializers to be enabled in pyrit_conf.

    Args:
        request: The incoming FastAPI request.
        initializer_name: Registry name of the initializer to remove.
    """
    _check_custom_initializers_allowed(request)
    service = get_initializer_service()

    try:
        await service.unregister_initializer_async(initializer_name=initializer_name)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Initializer '{initializer_name}' not found",
        ) from None
