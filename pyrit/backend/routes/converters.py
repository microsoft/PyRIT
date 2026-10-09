# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converters API routes.

Provides endpoints for managing converter instances and previewing conversions.
Converter types are set at app startup - you cannot add new types at runtime.
Converters created here are saved and restored when the backend restarts.
"""

from fastapi import APIRouter, HTTPException, Query, Request, status

from pyrit.backend.middleware.auth import has_admin_access
from pyrit.backend.models.common import IdentifierStr, ProblemDetail
from pyrit.backend.models.converters import (
    ConverterInstance,
    ConverterInstanceListResponse,
    ConverterPreviewRequest,
    ConverterPreviewResponse,
    ConverterTypeResponse,
    CreateConverterRequest,
    UpdateConverterRequest,
)
from pyrit.backend.routes.common import SAVED_INSTANCE_ERROR_RESPONSES, translate_saved_instance_errors
from pyrit.backend.services.converter_service import get_converter_service

router = APIRouter(prefix="/converters", tags=["converters"])


@router.get(
    "",
    response_model=ConverterInstanceListResponse,
)
async def list_converters() -> ConverterInstanceListResponse:  # pyrit-async-suffix-exempt
    """
    List converter instances.

    Returns all registered converter instances.

    Returns:
        ConverterInstanceListResponse: List of converter instances.
    """
    service = get_converter_service()
    return await service.list_converters_async()


@router.get(
    "/types",
    response_model=ConverterTypeResponse,
)
async def list_converter_types() -> ConverterTypeResponse:  # pyrit-async-suffix-exempt
    """
    List converter types projected from ``ConverterRegistry`` metadata.

    Returns:
        ConverterTypeResponse: Available converter types and build parameters.
    """
    service = get_converter_service()
    return await service.list_converter_types_async()


@router.post(
    "",
    response_model=ConverterInstance,
    status_code=status.HTTP_201_CREATED,
    responses=SAVED_INSTANCE_ERROR_RESPONSES,
)
async def create_converter(
    body: CreateConverterRequest, request: Request
) -> ConverterInstance:  # pyrit-async-suffix-exempt
    """
    Create and save a new converter instance.

    Instantiates a converter with the given type and parameters and saves it, so it is
    restored when the backend restarts. Supports nested converters via converter_id
    references in params.

    Returns:
        ConverterInstance: The created converter instance details and saved version.
    """
    service = get_converter_service()
    with translate_saved_instance_errors(action="create converter"):
        return await service.create_converter_async(request=body, is_admin=has_admin_access(request))


@router.get(
    "/{converter_id}",
    response_model=ConverterInstance,
    responses={
        404: {"model": ProblemDetail, "description": "Converter not found, or saved but not restored"},
    },
)
async def get_converter(converter_id: IdentifierStr) -> ConverterInstance:  # pyrit-async-suffix-exempt
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
            detail=service.describe_missing_converter(converter_id=converter_id),
        )

    return converter


@router.put(
    "/{converter_id}",
    response_model=ConverterInstance,
    responses={
        **SAVED_INSTANCE_ERROR_RESPONSES,
        404: {"model": ProblemDetail, "description": "No saved converter has this name"},
    },
)
async def update_converter(
    converter_id: IdentifierStr, body: UpdateConverterRequest, request: Request
) -> ConverterInstance:  # pyrit-async-suffix-exempt
    """
    Replace a saved converter.

    The request replaces the whole configuration: parameters and credentials it omits are removed.

    Returns:
        ConverterInstance: The replacement converter and its new saved version.
    """
    service = get_converter_service()
    with translate_saved_instance_errors(action="update converter"):
        return await service.update_converter_async(
            converter_id=converter_id, request=body, is_admin=has_admin_access(request)
        )


@router.delete(
    "/{converter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **SAVED_INSTANCE_ERROR_RESPONSES,
        404: {"model": ProblemDetail, "description": "Converter not found"},
        428: {"model": ProblemDetail, "description": "Version required to delete a saved converter"},
    },
)
async def delete_converter(
    converter_id: IdentifierStr,
    request: Request,
    version: IdentifierStr | None = Query(
        None, description="Version returned when the converter was read; required for saved converters"
    ),
) -> None:  # pyrit-async-suffix-exempt
    """Delete a converter instance by registry name, and its saved recipe if it is saved."""
    service = get_converter_service()
    with translate_saved_instance_errors(action="delete converter"):
        deleted = await service.delete_converter_async(
            converter_id=converter_id, expected_version=version, is_admin=has_admin_access(request)
        )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Converter '{converter_id}' not found",
        )


@router.post(
    "/preview",
    response_model=ConverterPreviewResponse,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid converter configuration"},
    },
)
async def preview_conversion(request: ConverterPreviewRequest) -> ConverterPreviewResponse:  # pyrit-async-suffix-exempt
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
