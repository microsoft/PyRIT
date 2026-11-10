# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""REST endpoints for scorer types and named scorer instances."""

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from pyrit.backend.models import (
    CreateScorerRequest,
    ProblemDetail,
    ScorerListResponse,
    ScorerTypeResponse,
)
from pyrit.backend.models.common import CursorStr, IdentifierStr
from pyrit.backend.models.scorers import (
    UnregisteredScorer,
)
from pyrit.backend.routes.common import (
    NOT_SAVEABLE_PROBLEM_TYPE,
    not_saveable_response,
    saved_entry_owner_from_request,
)
from pyrit.backend.services.saved_entries import NotSaveableError, SavedEntryConflictError, SavedEntryStorageError
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.models.catalog import ScorerInstance

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
    cursor: CursorStr | None = Query(None, description="Scorer registry name to start after"),
) -> ScorerListResponse:  # pyrit-async-suffix-exempt
    """
    List named scorer instances with pagination.

    Returns:
        ScorerListResponse: The requested page of scorer instances.
    """
    return await get_scorer_service().list_scorers_async(limit=limit, cursor=cursor)


@router.post(
    "",
    response_model=ScorerInstance | UnregisteredScorer,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {
            "model": ProblemDetail,
            "description": (
                f"Invalid scorer type, parameters, or name, or (type {NOT_SAVEABLE_PROBLEM_TYPE}) a scorer that "
                "can't be saved as asked but can be created without saving it"
            ),
        },
        409: {"model": ProblemDetail, "description": "A scorer to save has the name of a registered or saved scorer"},
        503: {"model": ProblemDetail, "description": "Saved scorers can't be written right now"},
    },
)
async def create_scorer(
    *,
    body: CreateScorerRequest,
    request: Request,
) -> ScorerInstance | UnregisteredScorer | JSONResponse:  # pyrit-async-suffix-exempt
    """
    Construct a scorer through ScorerRegistry, with optional registration.

    Returns:
        ScorerInstance | UnregisteredScorer | JSONResponse: A named instance or an unregistered descriptor, or the
            problem response for a scorer that can't be saved as asked.
    """
    try:
        return await get_scorer_service().create_scorer_async(
            request=body,
            owner=saved_entry_owner_from_request(request) if body.persist else None,
            recipes_source=getattr(request.app.state, "registry_recipes_source", None),
        )
    except NotSaveableError as exc:
        return not_saveable_response(detail=str(exc), request=request)
    except SavedEntryConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SavedEntryStorageError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
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
async def get_scorer(scorer_registry_name: IdentifierStr) -> ScorerInstance:  # pyrit-async-suffix-exempt
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
