# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Runtime technique catalog routes, guarded by shared authentication and admission."""

from fastapi import APIRouter, HTTPException, status

from pyrit.backend.models.common import IdentifierStr, ProblemDetail
from pyrit.backend.models.techniques import (
    CreateTechniqueRequest,
    TechniqueInstance,
    TechniqueListResponse,
    TechniqueTypeResponse,
)
from pyrit.backend.services.technique_service import get_technique_service

router = APIRouter(prefix="/techniques", tags=["techniques"])


@router.get("", response_model=TechniqueListResponse)
async def list_techniques_async() -> TechniqueListResponse:
    """
    List factories from the active catalog.

    Returns:
        TechniqueListResponse: Registered factories.
    """
    return await get_technique_service().list_async()


@router.get("/types", response_model=TechniqueTypeResponse)
async def technique_types_async() -> TechniqueTypeResponse:
    """
    Get attack constructor metadata for basic technique creation.

    Returns:
        TechniqueTypeResponse: Declared construction inputs.
    """
    return await get_technique_service().types_async()


@router.get("/{name}", response_model=TechniqueInstance, responses={404: {"model": ProblemDetail}})
async def get_technique_async(name: IdentifierStr) -> TechniqueInstance:
    """
    Get safe factory settings.

    Returns:
        TechniqueInstance: The named factory.
    """
    item = await get_technique_service().get_async(name)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Technique '{name}' was not found")
    return item


@router.post(
    "", response_model=TechniqueInstance, status_code=status.HTTP_201_CREATED, responses={400: {"model": ProblemDetail}}
)
async def create_technique_async(request: CreateTechniqueRequest) -> TechniqueInstance:
    """
    Create a runtime factory; execution inputs remain deferred.

    Returns:
        TechniqueInstance: The admitted factory.
    """
    try:
        return await get_technique_service().create_async(request)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
