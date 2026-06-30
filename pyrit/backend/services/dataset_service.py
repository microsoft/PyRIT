# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset service for listing and loading seed datasets.

Wraps ``SeedDatasetProvider`` discovery/fetching and ``CentralMemory`` so the
API can list available datasets and load them into memory. Mirrors the behavior
of the ``LoadDefaultDatasets`` initializer.
"""

import logging
from functools import lru_cache

from pyrit.backend.models.datasets import (
    DatasetInfo,
    DatasetListResponse,
    LoadDatasetRequest,
    LoadDatasetResponse,
    LoadedDataset,
)
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory

logger = logging.getLogger(__name__)

_ADDED_BY = "DatasetService"


class DatasetService:
    """Service for listing and loading seed datasets."""

    async def list_datasets_async(self) -> DatasetListResponse:
        """
        List all available datasets and whether they are already in memory.

        Returns:
            DatasetListResponse: Available datasets with their loaded status.
        """
        available = await SeedDatasetProvider.get_all_dataset_names_async()

        memory = CentralMemory.get_memory_instance()
        loaded = set(memory.get_seed_dataset_names())

        items = [DatasetInfo(name=name, loaded=name in loaded) for name in available]
        return DatasetListResponse(items=items)

    async def load_datasets_async(self, *, request: LoadDatasetRequest) -> LoadDatasetResponse:
        """
        Fetch the requested datasets and add their seeds to memory.

        Args:
            request: The dataset names to load and whether to cache them.

        Returns:
            LoadDatasetResponse: Summary of the datasets loaded and total seed count.

        Raises:
            ValueError: If any requested dataset name does not exist.
        """
        datasets = await SeedDatasetProvider.fetch_datasets_async(
            dataset_names=request.dataset_names,
            cache=request.cache,
        )

        memory = CentralMemory.get_memory_instance()
        await memory.add_seed_datasets_to_memory_async(datasets=datasets, added_by=_ADDED_BY)

        loaded_datasets = [
            LoadedDataset(name=dataset.dataset_name or "unknown", seed_count=len(dataset.seeds)) for dataset in datasets
        ]
        total_seeds = sum(item.seed_count for item in loaded_datasets)

        logger.info(f"Loaded {len(loaded_datasets)} datasets ({total_seeds} seeds) into memory")
        return LoadDatasetResponse(loaded_datasets=loaded_datasets, total_seeds=total_seeds)


@lru_cache(maxsize=1)
def get_dataset_service() -> DatasetService:
    """
    Get the global dataset service instance.

    Returns:
        The singleton DatasetService instance.
    """
    return DatasetService()
