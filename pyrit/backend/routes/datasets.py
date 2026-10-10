# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset API routes.

Lists available seed datasets and browses the stored seed examples of one dataset. Datasets are
discovered from registered ``SeedDatasetProvider`` subclasses and from memory.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from pyrit.backend.models.common import MAX_ITEMS, CursorStr, ProblemDetail
from pyrit.backend.models.datasets import (
    DatasetListResponse,
    SeedExampleDetailResponse,
    SeedExampleListResponse,
)
from pyrit.backend.services.dataset_service import get_dataset_service
from pyrit.models import PromptDataType, SeedType

router = APIRouter(prefix="/datasets", tags=["datasets"])

_SELECTION_KEY_DESCRIPTION = "The selection_key of a dataset from GET /datasets"


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
    selection_key: str = Query(..., description=_SELECTION_KEY_DESCRIPTION),
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
    modality: list[PromptDataType] | None = Query(None, max_length=MAX_ITEMS, description="Data types, OR-matched"),
    harm_category: list[str] | None = Query(
        None, max_length=MAX_ITEMS, description="Whole harm categories, case-insensitive, OR-matched"
    ),
    seed_type: list[SeedType] | None = Query(None, max_length=MAX_ITEMS, description="Seed types, OR-matched"),
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
        data_types=modality,
        harm_categories=harm_category,
        seed_types=seed_type,
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
    selection_key: str = Query(..., description=_SELECTION_KEY_DESCRIPTION),
) -> SeedExampleDetailResponse:
    """
    Get one stored logical seed example with all of its members.

    Returns:
        SeedExampleDetailResponse: The example and its members.

    Raises:
        HTTPException: If the dataset does not contain the example.
    """
    example = await get_dataset_service().get_seed_example_async(selection_key=selection_key, example_id=example_id)
    if example is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Seed example '{example_id}' not found")
    return example
