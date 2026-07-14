# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset API routes.

Provides an endpoint for listing available seed datasets. Datasets are
discovered from registered ``SeedDatasetProvider`` subclasses.
"""

from fastapi import APIRouter

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.datasets import (
    DatasetListResponse,
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
    List all available datasets.

    Returns:
        DatasetListResponse: Available datasets.
    """
    service = get_dataset_service()
    return await service.list_datasets_async()
