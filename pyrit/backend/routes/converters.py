# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converters API routes.

Provides endpoints for managing converter instances and previewing conversions.
Converter types are set at app startup - you cannot add new types at runtime.
"""

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from pyrit.backend.models import (
    ConverterInstance,
    ConverterInstanceListResponse,
    ConverterPreviewRequest,
    ConverterPreviewResponse,
    ConverterTypeResponse,
    CreateConverterRequest,
    ProblemDetail,
)
from pyrit.backend.models.common import IdentifierStr, NotSaveableProblemDetail, SaveabilityResponse
from pyrit.backend.models.converters import (
    ConverterSaveabilityRequest,
    UnregisteredConverter,
)
from pyrit.backend.routes.common import (
    NOT_SAVEABLE_PROBLEM_TYPE,
    not_saveable_response,
    saved_entry_owner_from_request,
)
from pyrit.backend.services import get_converter_service
from pyrit.backend.services.saved_entries import SavedEntryRemovalError
from pyrit.registry import NotSaveableError, SavedEntryConflictError, SavedEntryStorageError
from pyrit.registry.instance_registry import RegistryNameTakenError

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
    "/saveability",
    response_model=SaveabilityResponse,
    responses={400: {"model": ProblemDetail, "description": "Invalid converter type or parameters"}},
)
async def check_converter_saveability(  # pyrit-async-suffix-exempt
    *,
    body: ConverterSaveabilityRequest,
) -> SaveabilityResponse:
    """
    Say whether a converter could be saved as asked, without building or saving it.

    Only the type and parameters are checked, by the checks saving runs before the converter is built. The name isn't
    checked, and the converter isn't built, so its constructor's own checks don't run: a null reason means the save
    checks pass, not that creating the converter succeeds.

    Returns:
        SaveabilityResponse: Why the converter can't be saved, or no reason when the save checks pass.
    """
    try:
        return await get_converter_service().check_saveability_async(request=body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post(
    "",
    response_model=ConverterInstance | UnregisteredConverter,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {
            "model": NotSaveableProblemDetail,
            "description": (
                f"Invalid converter type or parameters, or (type {NOT_SAVEABLE_PROBLEM_TYPE}) a converter that "
                "can't be saved as asked but can be created without saving it"
            ),
        },
        409: {
            "model": ProblemDetail,
            "description": "A registered converter has the name, or, for a converter to save, a saved converter has it",
        },
        503: {"model": ProblemDetail, "description": "Saved converters can't be written right now"},
    },
)
async def create_converter(
    *,
    body: CreateConverterRequest,
    request: Request,
) -> ConverterInstance | UnregisteredConverter | JSONResponse:  # pyrit-async-suffix-exempt
    """
    Create a new converter instance.

    Instantiates a converter with the given type and parameters, with optional registration.
    Supports nested converters via converter_id references in params.

    Returns:
        ConverterInstance | UnregisteredConverter | JSONResponse: A named instance or an unregistered descriptor,
            or the problem response for a converter that can't be saved as asked.
    """
    service = get_converter_service()

    try:
        return await service.create_converter_async(
            request=body,
            owner=saved_entry_owner_from_request(request) if body.persist else None,
            recipes_source=getattr(request.app.state, "registry_recipes_source", None),
        )
    except NotSaveableError as e:
        return not_saveable_response(error=e, request=request)
    except (SavedEntryConflictError, RegistryNameTakenError) as e:
        # RegistryNameTakenError is a ValueError, and a taken name is a 409 whether or not the converter is saved.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except SavedEntryStorageError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
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
            detail=f"Converter '{converter_id}' not found",
        )

    return converter


@router.delete(
    "/{converter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ProblemDetail, "description": "Converter not found"},
        409: {"model": ProblemDetail, "description": "The converter is saved, so it can't be removed yet"},
    },
)
async def delete_converter(converter_id: IdentifierStr) -> None:  # pyrit-async-suffix-exempt
    """Delete a converter instance by registry name."""
    service = get_converter_service()
    try:
        removed = await service.delete_converter_async(converter_id=converter_id)
    except SavedEntryRemovalError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    if not removed:
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
