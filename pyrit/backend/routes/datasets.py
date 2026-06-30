# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset API routes.

Provides endpoints for listing available seed datasets and loading them into
memory. Datasets are discovered from registered ``SeedDatasetProvider``
subclasses.
"""

from fastapi import APIRouter, HTTPException, status

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.datasets import (
    DatasetListResponse,
    LoadDatasetRequest,
    LoadDatasetResponse,
)
from pyrit.backend.services.dataset_service import get_dataset_service

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get(
    "",
    response_model=DatasetListResponse,
    responses={
        500: {"model": ProblemDetail, "description": "Internal server error"},
    },
)
async def list_datasets() -> DatasetListResponse:  # pyrit-async-suffix-exempt
    """
    List all available datasets and whether they are already loaded in memory.

    Returns:
        DatasetListResponse: Available datasets with their loaded status.
    """
    service = get_dataset_service()
    return await service.list_datasets_async()


@router.post(
    "/load",
    response_model=LoadDatasetResponse,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid dataset name"},
    },
)
async def load_datasets(request: LoadDatasetRequest) -> LoadDatasetResponse:  # pyrit-async-suffix-exempt
    """
    Load one or more datasets into memory.

    Returns:
        LoadDatasetResponse: Summary of the datasets loaded and total seed count.
    """
    service = get_dataset_service()
    try:
        return await service.load_datasets_async(request=request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load datasets: {str(e)}",
        ) from e
