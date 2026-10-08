# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""REST endpoints for scorer types and named scorer instances."""

from fastapi import APIRouter, HTTPException, Query, status

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scorers import (
    CreateScorerRequest,
    ScorerListResponse,
    ScorerTypeResponse,
)
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
    responses={400: {"model": ProblemDetail, "description": "Invalid scorer type, parameters, or name"}},
)
async def create_scorer(request: CreateScorerRequest) -> ScorerInstance:  # pyrit-async-suffix-exempt
    """
    Construct a scorer through ScorerRegistry and register it under its name.

    Returns:
        ScorerInstance: The registered scorer and complete identifier.
    """
    try:
        return await get_scorer_service().create_scorer_async(request=request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create scorer: {exc}",
        ) from exc


@router.get(
    "/{scorer_registry_name}",
    response_model=ScorerInstance,
    responses={404: {"model": ProblemDetail, "description": "Scorer not found"}},
)
async def get_scorer(scorer_registry_name: str) -> ScorerInstance:  # pyrit-async-suffix-exempt
    """
    Get a named scorer instance.

    Returns:
        ScorerInstance: The requested scorer.
    """
    scorer = await get_scorer_service().get_scorer_async(scorer_registry_name=scorer_registry_name)
    if scorer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scorer '{scorer_registry_name}' not found",
        )
    return scorer
