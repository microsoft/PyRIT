# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset API routes.

Uploads and deletes named seed datasets, lists available datasets, and browses stored examples.
Datasets are discovered from registered ``SeedDatasetProvider`` subclasses and from memory.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import Field

from pyrit.backend.models import (
    DatasetListResponse,
    ProblemDetail,
)
from pyrit.backend.models.common import (
    MAX_CURSOR_LENGTH,
    MAX_ITEMS,
    MAX_LABEL_VALUE_LENGTH,
    CursorStr,
)
from pyrit.backend.models.datasets import (
    DatasetDeletionResponse,
    DatasetMutationResponse,
    DatasetUploadResponse,
    SeedExampleDetailResponse,
    SeedExampleListResponse,
)
from pyrit.backend.services import get_dataset_service
from pyrit.backend.services.dataset_upload_service import upload_dataset_async
from pyrit.models import PromptDataType, SeedType

router = APIRouter(prefix="/datasets", tags=["datasets"])

_SELECTION_KEY_DESCRIPTION = "The selection_key of a dataset from GET /datasets"


@router.post(
    "",
    response_model=DatasetUploadResponse | DatasetMutationResponse,
    status_code=201,
    responses={
        200: {"model": DatasetMutationResponse, "description": "Atomic batch committed; cleanup may be pending"},
        404: {"model": ProblemDetail, "description": "Update dataset or exact record ID not found"},
        **{
            code: {"model": ProblemDetail, "description": description}
            for code, description in {
                409: "Dataset name already stored/reserved, or stale expected_version",
                413: "Request, manifest, or prompt count exceeds configured limits",
                415: "Use multipart/form-data",
                422: "Invalid manifest, prompt, or multipart reference",
                503: "Upload failed and cleanup remains pending; see Location",
            }.items()
        },
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["manifest"],
                        "properties": {
                            "manifest": {
                                "type": "string",
                                "description": (
                                    'JSON: {"name":"example","seeds":[{"seed_type":"prompt",'
                                    '"data_type":"binary_path","value":"upload:document"}]}. '
                                    "Other named parts contain referenced files. Defaults: 100 MiB request, "
                                    "10 MiB JSON, 10,000 prompts. Each file part must be referenced."
                                    " Default mode=create; mode=update requires expected_version and "
                                    "seeds (no ID=add, ID=replace) and/or delete_seed_ids (record UUIDs). "
                                    "Omitted records stay unchanged. GET seeds with editable=true supplies "
                                    "snapshot-consistent version and editable_seeds with retained media handles."
                                ),
                            }
                        },
                        "additionalProperties": {"type": "string", "format": "binary"},
                    }
                }
            },
        }
    },
)
async def upload_dataset_route_async(
    request: Request, response: Response
) -> DatasetUploadResponse | DatasetMutationResponse:
    """
    Create a dataset atomically from a JSON manifest and inert media file parts.

    Returns:
        DatasetUploadResponse: The committed dataset name and actual population counts.
    """
    result = await upload_dataset_async(request)
    if isinstance(result, DatasetMutationResponse):
        response.status_code = 200
    if result.cleanup_location is not None:
        response.headers["Location"] = result.cleanup_location
    return result


@router.delete(
    "",
    response_model=DatasetDeletionResponse,
    responses={
        202: {"model": DatasetDeletionResponse, "description": "Seeds removed; owned-media cleanup pending"},
        400: {"model": ProblemDetail, "description": "Named selection key required"},
        404: {"model": ProblemDetail, "description": "No stored dataset with this exact name"},
    },
)
async def delete_dataset_route_async(
    response: Response,
    selection_key: str = Query(..., max_length=MAX_CURSOR_LENGTH, description=_SELECTION_KEY_DESCRIPTION),
) -> DatasetDeletionResponse:
    """
    Permanently delete exact-name stored seeds, not providers or shared/external media.

    Returns:
        DatasetDeletionResponse: Durable cleanup status and deleted seed count.
    """
    result = await get_dataset_service().delete_dataset_async(selection_key=selection_key)
    response.status_code = 202 if result.status == "pending" else 200
    response.headers["Location"] = f"/api/datasets/deletions/{result.deletion_id}"
    return result


@router.get(
    "/deletions/{deletion_id}",
    response_model=DatasetDeletionResponse,
    responses={404: {"model": ProblemDetail, "description": "Dataset deletion not found"}},
)
async def get_dataset_deletion_async(deletion_id: UUID) -> DatasetDeletionResponse:
    """
    Read durable cleanup status and retry pending owned-media cleanup inline.

    Returns:
        DatasetDeletionResponse: The current cleanup outcome.

    """
    return await get_dataset_service().get_dataset_deletion_async(deletion_id)


@router.get(
    "",
    response_model=DatasetListResponse,
    responses={
        500: {"model": ProblemDetail, "description": "Internal server error"},
    },
)
async def list_datasets(loaded_only: bool = False) -> DatasetListResponse:  # pyrit-async-suffix-exempt
    """
    List all available datasets.

    Args:
        loaded_only (bool): When True, return only datasets that have seeds in memory.

    Returns:
        DatasetListResponse: Available datasets.
    """
    service = get_dataset_service()
    return await service.list_datasets_async(loaded_only=loaded_only)


@router.get(
    "/seeds",
    response_model=SeedExampleListResponse,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid selection key or cursor"},
    },
)
async def list_seed_examples(  # pyrit-async-suffix-exempt
    selection_key: str = Query(..., max_length=MAX_CURSOR_LENGTH, description=_SELECTION_KEY_DESCRIPTION),
    limit: int = Query(20, ge=1, le=100, description="Maximum examples per page"),
    cursor: CursorStr | None = Query(
        None,
        description="The next_cursor of the previous page. A cursor is valid only with the same "
        "selection_key and filters.",
    ),
    search: str | None = Query(
        None,
        max_length=1000,
        description="Case-insensitive literal text to find in text prompt and objective values",
    ),
    value_pattern: str | None = Query(
        None,
        max_length=1000,
        description="Full case-insensitive LIKE pattern instead of search: % and _ wildcards, / escaping",
    ),
    modality: list[PromptDataType] | None = Query(None, max_length=MAX_ITEMS, description="Data types, OR-matched"),
    harm_category: list[Annotated[str, Field(max_length=MAX_LABEL_VALUE_LENGTH)]] | None = Query(
        None, max_length=MAX_ITEMS, description="Whole harm categories, case-insensitive, OR-matched"
    ),
    seed_type: list[SeedType] | None = Query(None, max_length=MAX_ITEMS, description="Seed types, OR-matched"),
    editable: bool = Query(False, description="Include submit-ready replacements and a snapshot-consistent version"),
) -> SeedExampleListResponse:
    """
    List one page of the stored logical seed examples of a dataset.

    Examples are ordered newest first. Different filters are AND-matched, and different members
    of an example can match different filters.

    Returns:
        SeedExampleListResponse: The page, its pagination data, and the number of matching examples.
    """
    return await get_dataset_service().list_seed_examples_async(
        selection_key=selection_key,
        limit=limit,
        cursor=cursor,
        search=search,
        value_pattern=value_pattern,
        data_types=modality,
        harm_categories=harm_category,
        seed_types=seed_type,
        editable=editable,
    )


@router.get(
    "/seeds/{example_id}",
    response_model=SeedExampleDetailResponse,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid selection key"},
        404: {"model": ProblemDetail, "description": "Seed example not found in the dataset"},
    },
)
async def get_seed_example(  # pyrit-async-suffix-exempt
    example_id: UUID,
    selection_key: str = Query(..., max_length=MAX_CURSOR_LENGTH, description=_SELECTION_KEY_DESCRIPTION),
    editable: bool = Query(False, description="Include submit-ready replacements and a snapshot-consistent version"),
) -> SeedExampleDetailResponse:
    """
    Get one stored logical seed example with all of its members.

    Returns:
        SeedExampleDetailResponse: The example and its members.

    Raises:
        HTTPException: If the dataset does not contain the example.
    """
    example = await get_dataset_service().get_seed_example_async(
        selection_key=selection_key, example_id=example_id, editable=editable
    )
    if example is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Seed example '{example_id}' not found")
    return example
