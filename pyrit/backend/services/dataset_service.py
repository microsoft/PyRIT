# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset service for listing seed datasets.

Wraps ``SeedDatasetProvider`` discovery and memory to list available datasets.
"""

import logging
from functools import lru_cache

from pyrit.backend.models.datasets import (
    DatasetInfo,
    DatasetListResponse,
)
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory

logger = logging.getLogger(__name__)


class DatasetService:
    """Service for listing seed datasets."""

    def __init__(self) -> None:
        """Initialize the dataset service."""
        self._memory = CentralMemory.get_memory_instance()

    async def list_datasets_async(self) -> DatasetListResponse:
        """
        List all available datasets.

        Combines datasets discoverable via registered providers with those
        already loaded into memory, since both are available for use.

        Returns:
            DatasetListResponse: Available datasets.
        """
        provider_names = await SeedDatasetProvider.get_all_dataset_names_async()
        memory_names = self._memory.get_seed_dataset_names()
        available = sorted(set(provider_names) | set(memory_names))
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
