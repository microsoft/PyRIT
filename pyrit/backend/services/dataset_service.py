# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset service for listing seed datasets.

Wraps ``SeedDatasetProvider`` discovery so the API can list available datasets.
"""

import logging
from functools import lru_cache

from pyrit.backend.models.datasets import (
    DatasetInfo,
    DatasetListResponse,
)
from pyrit.datasets import SeedDatasetProvider

logger = logging.getLogger(__name__)


class DatasetService:
    """Service for listing seed datasets."""

    async def list_datasets_async(self) -> DatasetListResponse:
        """
        List all available datasets.

        Returns:
            DatasetListResponse: Available datasets.
        """
        available = await SeedDatasetProvider.get_all_dataset_names_async()
        items = [DatasetInfo(name=name) for name in available]
        return DatasetListResponse(items=items)


@lru_cache(maxsize=1)
def get_dataset_service() -> DatasetService:
    """
    Get the global dataset service instance.

    Returns:
        The singleton DatasetService instance.
    """
    return DatasetService()
