# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Runtime technique catalog routes, guarded by shared authentication and admission."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from pyrit.backend.middleware.auth import current_user_oid
from pyrit.backend.models import ProblemDetail
from pyrit.backend.models.common import CursorStr, IdentifierStr
from pyrit.backend.models.techniques import (
    CreateTechniqueRequest,
    TechniqueConfiguration,
    TechniqueEditResponse,
    TechniqueInstance,
    TechniqueListResponse,
    TechniqueTypeResponse,
)
from pyrit.backend.routes.common import NOT_SAVEABLE_PROBLEM_TYPE, not_saveable_response, saved_entry_owner_from_request
from pyrit.backend.services.technique_edit_service import TechniqueEditService
from pyrit.backend.services.technique_service import get_technique_service
from pyrit.models.technique_edit import TechniqueOverride
from pyrit.registry import NotSaveableError, SavedEntryConflictError, SavedEntryStorageError

router = APIRouter(prefix="/techniques", tags=["techniques"])


@router.get("", response_model=TechniqueListResponse)
async def list_techniques_async(
    *,
    user_oid: Annotated[str | None, Depends(current_user_oid)],
    limit: int = Query(50, ge=1, le=200, description="Maximum items per page"),
    cursor: CursorStr | None = Query(None, description="Pagination cursor (technique registry name)"),
) -> TechniqueListResponse:
    """
    List factories from the active catalog with pagination.

    Returns:
        TechniqueListResponse: A page of registered factories.
    """
    return await get_technique_service().list_async(limit=limit, cursor=cursor, current_user_oid=user_oid)


@router.get("/types", response_model=TechniqueTypeResponse)
async def technique_types_async() -> TechniqueTypeResponse:
    """
    Get attack constructor metadata for basic technique creation.

    Returns:
        TechniqueTypeResponse: Declared construction inputs.
    """
    return await get_technique_service().types_async()


@router.get("/{name}", response_model=TechniqueInstance, responses={404: {"model": ProblemDetail}})
async def get_technique_async(
    *, name: IdentifierStr, user_oid: Annotated[str | None, Depends(current_user_oid)]
) -> TechniqueInstance:
    """
    Get safe factory settings.

    Returns:
        TechniqueInstance: The named factory.
    """
    item = await get_technique_service().get_async(name=name, current_user_oid=user_oid)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Technique '{name}' was not found")
    return item


@router.get("/{name}/configuration", response_model=TechniqueConfiguration)
async def technique_configuration_async(name: IdentifierStr) -> TechniqueConfiguration:
    """
    Load effective editable settings without constructing an attack.

    Returns:
        TechniqueConfiguration: Safe settings for the named source.
    """
    try:
        return await asyncio.to_thread(TechniqueEditService.configuration, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{name}/edit", response_model=TechniqueEditResponse)
async def validate_technique_edit_async(name: IdentifierStr, request: TechniqueOverride) -> TechniqueEditResponse:
    """
    Validate a private edit; no factory is registered.

    Returns:
        TechniqueEditResponse: A validated run-local specification.
    """
    if name != request.source_name:
        raise HTTPException(status_code=400, detail="Source technique name does not match the edit")
    try:
        return await asyncio.to_thread(TechniqueEditService.validate, request)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "",
    response_model=TechniqueInstance,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {
            "model": ProblemDetail,
            "description": (
                f"Invalid technique, or (type {NOT_SAVEABLE_PROBLEM_TYPE}) a technique that can't be saved as asked "
                "but can be created without saving it"
            ),
        },
        409: {
            "model": ProblemDetail,
            "description": "A technique to save has the name or a tag of a registered technique",
        },
        503: {"model": ProblemDetail, "description": "Saved techniques can't be written right now"},
    },
)
async def create_technique_async(*, body: CreateTechniqueRequest, request: Request) -> TechniqueInstance | JSONResponse:
    """
    Create a runtime factory; execution inputs remain deferred.

    Returns:
        TechniqueInstance | JSONResponse: The admitted factory, or the problem response for a technique that can't
            be saved as asked.
    """
    try:
        return await get_technique_service().create_async(
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
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
