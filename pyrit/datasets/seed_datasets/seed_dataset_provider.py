# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import fields as dc_fields
from typing import Any

from tqdm import tqdm

from pyrit.datasets.seed_datasets.seed_metadata import SeedDatasetFilter, SeedDatasetLoadTime, SeedDatasetMetadata
from pyrit.models.seeds import SeedDataset

logger = logging.getLogger(__name__)


class SeedDatasetProvider(ABC):
    """
    Abstract base class for providing seed datasets with automatic registration.

    All concrete subclasses are automatically registered and can be discovered
    via get_all_providers() class method. This enables automatic discovery of
    both local and remote dataset providers.

    Subclasses must implement:
    - fetch_dataset_async(): Fetch and return the dataset as a SeedDataset
    - dataset_name property: Human-readable name for the dataset

    All subclasses also have a _metadata property that is optional to make
    dataset addition easier, but failing to complete it makes downstream
    analysis more difficult.
    """

    _registry: dict[str, type["SeedDatasetProvider"]] = {}
    load_time: SeedDatasetLoadTime = SeedDatasetLoadTime.UNINITIALIZED

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Automatically register non-abstract subclasses.

        This is called when a class inherits from SeedDatasetProvider. The
        keyword-only ``__init__`` contract is enforced via
        ``enforce_keyword_only_init`` before concrete providers are registered.
        """
        super().__init_subclass__(**kwargs)
        # Local import to avoid a circular dependency at package init time.
        from pyrit.common.brick_contract import enforce_keyword_only_init

        enforce_keyword_only_init(cls, base_name="SeedDatasetProvider")
        if not inspect.isabstract(cls) and getattr(cls, "should_register", True):
            SeedDatasetProvider._registry[cls.__name__] = cls
            logger.debug(f"Registered dataset provider: {cls.__name__}")

    @property
    @abstractmethod
    def dataset_name(self) -> str:
        """
        The human-readable name of the dataset.

        Returns:
            str: The dataset name (e.g., "HarmBench", "JailbreakBench JBB-Behaviors")
        """

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the dataset and return as a SeedDataset.

        Subclasses MUST override this method.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.
                   Remote datasets will use DB_DATA_PATH for caching.

        Returns:
            SeedDataset: The fetched dataset with prompts.

        Raises:
            NotImplementedError: If the subclass does not override this method.
            Exception: If the dataset cannot be fetched or processed.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement fetch_dataset_async.")

    async def _parse_metadata_async(self) -> SeedDatasetMetadata | None:
        """
        Parse provider-specific metadata into the shared schema.

        Subclasses can override this to source metadata from class attributes,
        prompt files, or any other backing format. The default implementation
        returns None, which means metadata is not available for this provider.

        Returns:
            SeedDatasetMetadata | None: Parsed metadata for this provider, or None.
        """
        return None

    @classmethod
    def get_all_providers(cls) -> dict[str, type["SeedDatasetProvider"]]:
        """
        Get all registered dataset provider classes.

        Returns:
            dict[str, type[SeedDatasetProvider]]: Dictionary mapping class names to provider classes.
        """
        return cls._registry.copy()

    @classmethod
    async def get_all_dataset_names_async(cls, filters: SeedDatasetFilter | None = None) -> list[str]:
        """
        Get the names of all registered datasets.

        Args:
            filters (SeedDatasetFilter | None): List of filters to apply.

        Returns:
            list[str]: List of dataset names from all registered providers.

        Raises:
            ValueError: If no providers are registered or if providers cannot be instantiated.

        Example:
            >>> names = await SeedDatasetProvider.get_all_dataset_names_async()
            >>> print(f"Available datasets: {', '.join(names)}")
        """
        dataset_names = set()
        for provider_class in cls._registry.values():
            try:
                # Instantiate to get dataset name
                provider = provider_class()

                # Parser ensures a standard metadata format
                metadata = await provider._parse_metadata_async()

                if filters:
                    # "all" bypasses metadata filtering and returns every dataset
                    if filters.has_all_tag:
                        dataset_names.add(provider.dataset_name)
                        continue

                    # Datasets without metadata are skipped for all other filters
                    if not metadata:
                        continue

                    # Filters detected but no match -> don't add this dataset
                    if not cls._match_filter_to_metadata(metadata=metadata, dataset_filter=filters):
                        continue

                dataset_names.add(provider.dataset_name)
            except Exception as e:
                raise ValueError(f"Could not get dataset name from {provider_class.__name__}: {e}") from e
        return sorted(dataset_names)

    @classmethod
    def _match_filter_to_metadata(cls, metadata: SeedDatasetMetadata, dataset_filter: SeedDatasetFilter) -> bool:
        """
        Match a dataset's metadata against filter criteria.

        A dataset matches if ANY criterion in filters.criteria matches (OR across
        criteria). Within each criterion, ALL specified fields must match (AND
        across fields). Within each field:
        - strict_match=False: any overlap suffices (set intersection)
        - strict_match=True: all filter values must be present (filter is subset)

        Special tags:
        - "all": bypasses all filtering, returns True immediately.
        - "default": without strict_match, matches if the dataset has "default" tag.

        Args:
            metadata: The dataset's metadata.
            dataset_filter: The user-provided filter.

        Returns:
            Whether the metadata matches any criterion.
        """
        # "all" always bypasses
        if dataset_filter.has_all_tag:
            return True

        return any(
            cls._match_single_criterion(metadata=metadata, criterion=c, strict_match=dataset_filter.strict_match)
            for c in dataset_filter.criteria
        )

    @classmethod
    def _match_single_criterion(
        cls,
        *,
        metadata: SeedDatasetMetadata,
        criterion: SeedDatasetMetadata,
        strict_match: bool,
    ) -> bool:
        """
        Match a single SeedDatasetMetadata criterion against dataset metadata.

        Args:
            metadata: The dataset's real metadata.
            criterion: A single filter criterion.
            strict_match: Whether to require all filter values (AND) vs any overlap (OR).

        Returns:
            Whether the metadata satisfies this criterion.
        """
        # "default" shortcut (only without strict_match):
        # When the filter asks for "default" and the dataset has "default" in its
        # tags, match immediately. This lets "default" act as a curated-set marker
        # that bypasses other filter axes. With strict_match, "default" is treated
        # as a normal tag and must satisfy the full subset check.
        if (
            not strict_match
            and criterion.tags
            and "default" in criterion.tags
            and metadata.tags
            and "default" in metadata.tags
        ):
            return True

        for field in dc_fields(SeedDatasetMetadata):
            filter_vals = getattr(criterion, field.name)
            meta_vals = getattr(metadata, field.name)

            if filter_vals is None or meta_vals is None:
                continue

            if strict_match:
                if filter_vals - meta_vals:
                    return False
            else:
                if not (filter_vals & meta_vals):
                    return False

        return True

    @classmethod
    async def fetch_datasets_async(
        cls,
        *,
        dataset_names: list[str] | None = None,
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
            list[SeedDataset]: List of all fetched datasets.

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
            available_names = await cls.get_all_dataset_names_async()
            invalid_names = [name for name in dataset_names if name not in available_names]
            if invalid_names:
                raise ValueError(f"Dataset(s) not found: {invalid_names}. Available datasets: {available_names}")

        async def fetch_single_dataset_async(
            provider_name: str, provider_class: type["SeedDatasetProvider"]
        ) -> tuple[str, SeedDataset] | None:
            """
            Fetch a single dataset with error handling.

            Returns:
                tuple[str, SeedDataset] | None: Tuple of provider name and dataset, or None if filtered.
            """
            provider = provider_class()

            # Apply dataset name filter if specified
            if dataset_names is not None and provider.dataset_name not in dataset_names:
                logger.debug(f"Skipping {provider_name} - not in filter list")
                return None

            dataset = await provider.fetch_dataset_async(cache=cache)
            return (provider.dataset_name, dataset)

        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(max_concurrency)

        # Progress tracking
        total_count = len(cls._registry)
        pbar = tqdm(total=total_count, desc="Loading datasets - this can take a few minutes", unit="dataset")

        async def fetch_with_semaphore_async(
            provider_name: str, provider_class: type["SeedDatasetProvider"]
        ) -> tuple[str, SeedDataset] | None:
            """
            Enforce concurrency limit and update progress during dataset fetch.

            Returns:
                tuple[str, SeedDataset] | None: Tuple of provider name and dataset, or None if filtered.
            """
            async with semaphore:
                result = await fetch_single_dataset_async(provider_name, provider_class)
                pbar.update(1)
                return result

        # Fetch all datasets with controlled concurrency and progress bar
        tasks = [
            fetch_with_semaphore_async(provider_name, provider_class)
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
