# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from abc import ABC
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from datasets import DownloadMode, disable_progress_bars, load_dataset

from pyrit.common.path import DB_DATA_PATH
from pyrit.datasets import _remote_fetch
from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider

logger = logging.getLogger(__name__)

_CACHE_SUBDIR = "seed-prompt-entries"


class _RemoteDatasetLoader(SeedDatasetProvider, ABC):
    """
    Abstract base class for loading remote datasets that produce `SeedDataset`.

    Provides helper methods for fetching data from:
    - Public URLs (CSV, JSON, JSONL, TXT)
    - Local files
    - HuggingFace Hub

    Subclasses must implement:
    - fetch_dataset(): Fetch and return the dataset as a SeedDataset
    - dataset_name property: Human-readable name for the dataset
    """

    def _get_cache_file_name(self, *, source: str, file_type: str) -> str:
        return _remote_fetch.get_cache_file_name(source=source, file_type=file_type)

    def _validate_file_type(self, file_type: str) -> None:
        _remote_fetch.validate_file_type(file_type)

    def _read_cache(self, *, cache_file: Path, file_type: str) -> List[Dict[str, str]]:
        return _remote_fetch.read_cache(cache_file=cache_file, file_type=file_type)

    def _write_cache(self, *, cache_file: Path, examples: List[Dict[str, str]], file_type: str) -> None:
        _remote_fetch.write_cache(cache_file=cache_file, examples=examples, file_type=file_type)

    def _fetch_from_public_url(self, *, source: str, file_type: str) -> List[Dict[str, str]]:
        return _remote_fetch.fetch_from_public_url(source=source, file_type=file_type)

    def _fetch_from_file(self, *, source: str, file_type: str) -> List[Dict[str, str]]:
        return _remote_fetch.fetch_from_file(source=source, file_type=file_type)

    def _fetch_from_url(
        self,
        *,
        source: str,
        source_type: Literal["public_url", "file"] = "public_url",
        cache: bool = True,
    ) -> List[Dict[str, str]]:
        """
        Fetch examples from a URL or local file with caching under ``seed-prompt-entries``.

        Returns:
            The parsed examples.
        """
        return _remote_fetch.fetch_with_cache(
            source=source,
            source_type=source_type,
            cache_subdir=_CACHE_SUBDIR,
            cache=cache,
        )

    async def _fetch_from_huggingface(
        self,
        *,
        dataset_name: str,
        config: Optional[str] = None,
        split: Optional[str] = None,
        cache: bool = True,
        token: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Fetch a dataset from HuggingFace Hub.

        This is a helper method for datasets that are hosted on HuggingFace.
        The returned dataset object is the raw HuggingFace dataset, which
        subclasses should process into a SeedDataset.

        This method runs the synchronous load_dataset() in a thread pool to avoid
        blocking the event loop and enable true parallel execution.

        Args:
            dataset_name: HuggingFace dataset identifier (e.g., "JailbreakBench/JBB-Behaviors").
            config: Optional dataset configuration/subset name.
            split: Optional split to load (e.g., "train", "test"). If None, loads all splits.
            cache: Whether to cache the dataset. Defaults to True.
            token: Optional HuggingFace authentication token for gated datasets.
            **kwargs: Additional arguments to pass to load_dataset().

        Returns:
            The HuggingFace dataset object (DatasetDict or Dataset).

        Raises:
            ImportError: If datasets library is not installed.
            Exception: If the dataset cannot be loaded.

        Example:
            >>> data = await self._fetch_from_huggingface(
            ...     dataset_name="JailbreakBench/JBB-Behaviors",
            ...     config="behaviors",
            ...     split="train",
            ...     cache=True
            ... )
        """
        disable_progress_bars()

        def _load_dataset_sync():
            """
            Run dataset loading synchronously in thread pool.

            Returns:
                Dataset: The loaded dataset from Hugging Face.
            """
            cache_dir = str(DB_DATA_PATH / "huggingface") if cache else None

            dataset = load_dataset(
                dataset_name,
                config,
                split=split,
                cache_dir=cache_dir,
                download_mode=DownloadMode.REUSE_DATASET_IF_EXISTS,
                token=token,
                **kwargs,
            )
            return dataset

        try:
            dataset = await asyncio.to_thread(_load_dataset_sync)
            return dataset
        except Exception as e:
            logger.error(f"Failed to load HuggingFace dataset {dataset_name}: {e}")
            raise
