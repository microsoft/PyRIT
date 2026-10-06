# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""REST endpoints for scorer types and named scorer instances, which are saved and restored on restart."""

from fastapi import APIRouter, HTTPException, Query, Request, status

from pyrit.backend.middleware.auth import has_admin_access
from pyrit.backend.models.common import IdentifierStr, ProblemDetail
from pyrit.backend.models.scorers import (
    CreateScorerRequest,
    ScorerListResponse,
    ScorerTypeResponse,
    UpdateScorerRequest,
)
from pyrit.backend.routes.common import SAVED_INSTANCE_ERROR_RESPONSES, translate_saved_instance_errors
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.models.catalog.scorer import ScorerInstance

router = APIRouter(prefix="/scorers", tags=["scorers"])


@router.get("/types", response_model=ScorerTypeResponse)
async def list_scorer_types() -> ScorerTypeResponse:  # pyrit-async-suffix-exempt
    """
    List scorer classes and their registry-derived parameters.

    Returns:
        ScorerTypeResponse: Registered scorer types.
    """
    return await get_scorer_service().list_scorer_types_async()


@router.get("", response_model=ScorerListResponse)
async def list_scorers(
    limit: int = Query(50, ge=1, le=200, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Scorer registry name to start after"),
) -> ScorerListResponse:  # pyrit-async-suffix-exempt
    """
    List named scorer instances with pagination.

    Returns:
        ScorerListResponse: The requested page of scorer instances.
    """
    return await get_scorer_service().list_scorers_async(limit=limit, cursor=cursor)


@router.post(
    "",
    response_model=ScorerInstance,
    status_code=status.HTTP_201_CREATED,
    responses=SAVED_INSTANCE_ERROR_RESPONSES,
)
async def create_scorer(body: CreateScorerRequest, request: Request) -> ScorerInstance:  # pyrit-async-suffix-exempt
    """
    Construct a scorer through ScorerRegistry, save it, and register it under its name.

    Returns:
        ScorerInstance: The registered scorer, complete identifier, and saved version.
    """
    with translate_saved_instance_errors(action="create scorer"):
        return await get_scorer_service().create_scorer_async(request=body, is_admin=has_admin_access(request))


@router.get(
    "/{scorer_registry_name}",
    response_model=ScorerInstance,
    responses={404: {"model": ProblemDetail, "description": "Scorer not found, or saved but not restored"}},
)
async def get_scorer(scorer_registry_name: str) -> ScorerInstance:  # pyrit-async-suffix-exempt
    """
    Get a named scorer instance.

    Returns:
        ScorerInstance: The requested scorer.
    """
    service = get_scorer_service()
    scorer = await service.get_scorer_async(scorer_registry_name=scorer_registry_name)
    if scorer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=service.describe_missing_scorer(scorer_registry_name=scorer_registry_name),
        )
    return scorer


@router.put(
    "/{scorer_registry_name}",
    response_model=ScorerInstance,
    responses={
        **SAVED_INSTANCE_ERROR_RESPONSES,
        404: {"model": ProblemDetail, "description": "No saved scorer has this name"},
    },
)
async def update_scorer(
    scorer_registry_name: IdentifierStr, body: UpdateScorerRequest, request: Request
) -> ScorerInstance:  # pyrit-async-suffix-exempt
    """
    Replace a saved scorer.

    The request replaces the whole configuration: parameters and credentials it omits are removed.

    Returns:
        ScorerInstance: The replacement scorer and its new saved version.
    """
    with translate_saved_instance_errors(action="update scorer"):
        return await get_scorer_service().update_scorer_async(
            scorer_registry_name=scorer_registry_name, request=body, is_admin=has_admin_access(request)
        )


@router.delete(
    "/{scorer_registry_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **SAVED_INSTANCE_ERROR_RESPONSES,
        404: {"model": ProblemDetail, "description": "Scorer not found"},
        428: {"model": ProblemDetail, "description": "Version required to delete a saved scorer"},
    },
)
async def delete_scorer(
    scorer_registry_name: IdentifierStr,
    request: Request,
    version: IdentifierStr | None = Query(
        None, description="Version returned when the scorer was read; required for saved scorers"
    ),
) -> None:  # pyrit-async-suffix-exempt
    """Delete a saved scorer and unregister the scorer built from it."""
    with translate_saved_instance_errors(action="delete scorer"):
        deleted = await get_scorer_service().delete_scorer_async(
            scorer_registry_name=scorer_registry_name, expected_version=version, is_admin=has_admin_access(request)
        )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scorer '{scorer_registry_name}' not found",
        )
