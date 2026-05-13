# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer API routes.

Provides endpoints for listing available initializers and their metadata.

Route structure:
    /api/initializers           — list all initializers
    /api/initializers/{name}    — get single initializer detail
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.initializers import (
    ListRegisteredInitializersResponse,
    RegisteredInitializer,
)
from pyrit.backend.services.initializer_service import get_initializer_service

router = APIRouter(prefix="/initializers", tags=["initializers"])


@router.get(
    "",
    response_model=ListRegisteredInitializersResponse,
)
async def list_initializers(
    limit: int = Query(50, ge=1, le=200, description="Maximum items per page"),
    cursor: Optional[str] = Query(None, description="Pagination cursor (initializer_name to start after)"),
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
