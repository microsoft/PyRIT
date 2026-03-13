# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from tqdm import tqdm

from pyrit.models.seeds import SeedDataset

logger = logging.getLogger(__name__)


class SeedDatasetProvider(ABC):
    """
    Abstract base class for providing seed datasets with automatic registration.

    All concrete subclasses are automatically registered and can be discovered
    via get_all_providers() class method. This enables automatic discovery of
    both local and remote dataset providers.

    Subclasses must implement:
    - fetch_dataset(): Fetch and return the dataset as a SeedDataset
    - dataset_name property: Human-readable name for the dataset
    """

    _registry: dict[str, type["SeedDatasetProvider"]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Automatically register non-abstract subclasses.

        This is called when a class inherits from SeedDatasetProvider.
        """
        super().__init_subclass__(**kwargs)
        # Only register concrete (non-abstract) classes
        if not inspect.isabstract(cls) and getattr(cls, "should_register", True):
            SeedDatasetProvider._registry[cls.__name__] = cls
            logger.debug(f"Registered dataset provider: {cls.__name__}")

    @property
    @abstractmethod
    def dataset_name(self) -> str:
        """
        Return the human-readable name of the dataset.

        Returns:
            str: The dataset name (e.g., "HarmBench", "JailbreakBench JBB-Behaviors")
        """

    @abstractmethod
    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the dataset and return as a SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.
                   Remote datasets will use DB_DATA_PATH for caching.

        Returns:
            SeedDataset: The fetched dataset with prompts.

        Raises:
            Exception: If the dataset cannot be fetched or processed.
        """

    @classmethod
    def get_all_providers(cls) -> dict[str, type["SeedDatasetProvider"]]:
        """
        Get all registered dataset provider classes.

        Returns:
            Dict[str, Type[SeedDatasetProvider]]: Dictionary mapping class names to provider classes.
        """
        return cls._registry.copy()

    @classmethod
    def get_all_dataset_names(cls) -> list[str]:
        """
        Get the names of all registered datasets.

        Returns:
            List[str]: List of dataset names from all registered providers.

        Raises:
            ValueError: If no providers are registered or if providers cannot be instantiated.

        Example:
            >>> names = SeedDatasetProvider.get_all_dataset_names()
            >>> print(f"Available datasets: {', '.join(names)}")
        """
        dataset_names = set()
        for provider_class in cls._registry.values():
            try:
                # Instantiate to get dataset name
                provider = provider_class()
                dataset_names.add(provider.dataset_name)
            except Exception as e:
                raise ValueError(f"Could not get dataset name from {provider_class.__name__}: {e}") from e
        return sorted(dataset_names)

    @classmethod
    async def fetch_datasets_async(
        cls,
        *,
        dataset_names: Optional[list[str]] = None,
        cache: bool = True,
        max_concurrency: int = 5,
    ) -> list[SeedDataset]:
        """
        Fetch all registered datasets with optional filtering and caching.

        Datasets are fetched concurrently for improved performance.

        Args:
            dataset_names: Optional list of dataset names to fetch. If None, fetches all.
                          Names should match the dataset_name property of providers.
            cache: Whether to cache the fetched datasets. Defaults to True.
                   This uses DB_DATA_PATH for caching remote datasets.
            max_concurrency: Maximum number of datasets to fetch concurrently. Defaults to 5.
                            Set to 1 for fully sequential execution.

        Returns:
            List[SeedDataset]: List of all fetched datasets.

        Raises:
            ValueError: If any requested dataset_name does not exist.
            Exception: If any dataset fails to load.

        Example:
            >>> # Fetch all datasets
            >>> all_datasets = await SeedDatasetProvider.fetch_datasets_async()
            >>>
            >>> # Fetch specific datasets
            >>> specific = await SeedDatasetProvider.fetch_datasets_async(
            ...     dataset_names=["harmbench", "DarkBench"]
            ... )
        """
        # Validate dataset names if specified
        if dataset_names is not None:
            available_names = cls.get_all_dataset_names()
            invalid_names = [name for name in dataset_names if name not in available_names]
            if invalid_names:
                raise ValueError(f"Dataset(s) not found: {invalid_names}. Available datasets: {available_names}")

        async def fetch_single_dataset(
            provider_name: str, provider_class: type["SeedDatasetProvider"]
        ) -> Optional[tuple[str, SeedDataset]]:
            """
            Fetch a single dataset with error handling.

            Returns:
                Optional[Tuple[str, SeedDataset]]: Tuple of provider name and dataset, or None if filtered.
            """
            provider = provider_class()

            # Apply dataset name filter if specified
            if dataset_names is not None and provider.dataset_name not in dataset_names:
                logger.debug(f"Skipping {provider_name} - not in filter list")
                return None

            dataset = await provider.fetch_dataset(cache=cache)
            return (provider.dataset_name, dataset)

        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(max_concurrency)

        # Progress tracking
        total_count = len(cls._registry)
        pbar = tqdm(total=total_count, desc="Loading datasets - this can take a few minutes", unit="dataset")

        async def fetch_with_semaphore(
            provider_name: str, provider_class: type["SeedDatasetProvider"]
        ) -> Optional[tuple[str, SeedDataset]]:
            """
            Enforce concurrency limit and update progress during dataset fetch.

            Returns:
                Optional[Tuple[str, SeedDataset]]: Tuple of provider name and dataset, or None if filtered.
            """
            async with semaphore:
                result = await fetch_single_dataset(provider_name, provider_class)
                pbar.update(1)
                return result

        # Fetch all datasets with controlled concurrency and progress bar
        tasks = [
            fetch_with_semaphore(provider_name, provider_class)
            for provider_name, provider_class in cls._registry.items()
        ]

        results = await asyncio.gather(*tasks)
        pbar.close()

        # Merge datasets with the same name
        datasets: dict[str, SeedDataset] = {}
        for result in results:
            # Skip None results (filtered datasets)
            if result is None:
                continue

            dataset_name, dataset = result

            if dataset_name in datasets:
                logger.info(f"Merging multiple sources for {dataset_name}.")

                existing_dataset = datasets[dataset_name]
                combined_seeds = list(existing_dataset.seeds) + list(dataset.seeds)
                existing_dataset.seeds = combined_seeds
            else:
                datasets[dataset_name] = dataset

        logger.info(f"Successfully fetched {len(datasets)} unique datasets from {len(cls._registry)} providers")
        return list(datasets.values())
