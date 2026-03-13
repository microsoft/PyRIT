# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converters API routes.

Provides endpoints for managing converter instances and previewing conversions.
Converter types are set at app startup - you cannot add new types at runtime.
"""

from fastapi import APIRouter, HTTPException, status

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.converters import (
    ConverterInstance,
    ConverterInstanceListResponse,
    ConverterPreviewRequest,
    ConverterPreviewResponse,
    CreateConverterRequest,
    CreateConverterResponse,
)
from pyrit.backend.services.converter_service import get_converter_service

router = APIRouter(prefix="/converters", tags=["converters"])


@router.get(
    "",
    response_model=ConverterInstanceListResponse,
)
async def list_converters() -> ConverterInstanceListResponse:
    """
    List converter instances.

    Returns all registered converter instances.

    Returns:
        ConverterInstanceListResponse: List of converter instances.
    """
    service = get_converter_service()
    return await service.list_converters_async()


@router.post(
    "",
    response_model=CreateConverterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid converter type or parameters"},
    },
)
async def create_converter(request: CreateConverterRequest) -> CreateConverterResponse:
    """
    Create a new converter instance.

    Instantiates a converter with the given type and parameters.
    Supports nested converters via converter_id references in params.

    Returns:
        CreateConverterResponse: The created converter instance details.
    """
    service = get_converter_service()

    try:
        return await service.create_converter_async(request=request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create converter: {str(e)}",
        ) from e


@router.get(
    "/{converter_id}",
    response_model=ConverterInstance,
    responses={
        404: {"model": ProblemDetail, "description": "Converter not found"},
    },
)
async def get_converter(converter_id: str) -> ConverterInstance:
    """
    Get a converter instance by ID.

    Returns:
        ConverterInstance: The converter instance details.
    """
    service = get_converter_service()

    converter = await service.get_converter_async(converter_id=converter_id)
    if not converter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Converter '{converter_id}' not found",
        )

    return converter


@router.post(
    "/preview",
    response_model=ConverterPreviewResponse,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid converter configuration"},
    },
)
async def preview_conversion(request: ConverterPreviewRequest) -> ConverterPreviewResponse:
    """
    Preview conversion through a converter pipeline.

    Applies converters to the input and returns step-by-step results.
    Can use either converter_ids (existing instances) or inline converters.

    Returns:
        ConverterPreviewResponse: Original, converted values, and conversion steps.
    """
    service = get_converter_service()

    try:
        return await service.preview_conversion_async(request=request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Converter preview failed: {str(e)}",
        ) from e
