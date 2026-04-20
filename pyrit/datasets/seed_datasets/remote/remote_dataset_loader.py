# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
import tempfile
from abc import ABC
from collections.abc import Sequence
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from datasets import DownloadMode, disable_progress_bars, load_dataset

from pyrit.common.path import DB_DATA_PATH
from pyrit.datasets import _remote_fetch
from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.datasets.seed_datasets.seed_metadata import SeedDatasetMetadata

logger = logging.getLogger(__name__)

_CACHE_SUBDIR = "seed-prompt-entries"


class _RemoteDatasetLoader(SeedDatasetProvider, ABC):
    """
    Abstract base class for loading remote datasets.

    Provides helper methods for fetching data from:
    - Public URLs (CSV, JSON, JSONL, TXT)
    - Local files
    - HuggingFace Hub

    Subclasses must implement:
    - fetch_dataset(): Fetch and return the dataset as a SeedDataset
    - dataset_name property: Human-readable name for the dataset
    """

    @staticmethod
    def _validate_enums(
        values: Sequence[Enum],
        enum_cls: type[Enum],
        label: str,
    ) -> None:
        """
        Validate that all values are instances of the expected enum class.

        Args:
            values: List of values to validate.
            enum_cls: The enum class that all values must be instances of.
            label: Human-readable label for error messages (e.g. "category").

        Raises:
            ValueError: If any value is not an instance of the expected enum class.
        """
        for v in values:
            if not isinstance(v, enum_cls):
                valid = ", ".join(f"{enum_cls.__name__}.{m.name}" for m in enum_cls)
                raise ValueError(f"Expected {enum_cls.__name__}, got {type(v).__name__}: {v!r}. Valid values: {valid}")

    @staticmethod
    def _validate_enum(
        value: Enum,
        enum_cls: type[Enum],
        label: str,
    ) -> None:
        """
        Validate that a single value is an instance of the expected enum class.

        Args:
            value: The value to validate.
            enum_cls: The enum class that the value must be an instance of.
            label: Human-readable label for error messages (e.g. "severity").

        Raises:
            ValueError: If the value is not an instance of the expected enum class.
        """
        if not isinstance(value, enum_cls):
            valid = ", ".join(f"{enum_cls.__name__}.{m.name}" for m in enum_cls)
            raise ValueError(
                f"Expected {enum_cls.__name__}, got {type(value).__name__}: {value!r}. Valid values: {valid}"
            )

    def _get_cache_file_name(self, *, source: str, file_type: str) -> str:
        return _remote_fetch.get_cache_file_name(source=source, file_type=file_type)

    def _validate_file_type(self, file_type: str) -> None:
        _remote_fetch.validate_file_type(file_type)

    def _get_file_type(self, *, source: str) -> str:
        return _remote_fetch.get_file_type(source=source)

    def _read_cache(self, *, cache_file: Path, file_type: str) -> list[dict[str, str]]:
        return _remote_fetch.read_cache(cache_file=cache_file, file_type=file_type)

    def _write_cache(self, *, cache_file: Path, examples: list[dict[str, str]], file_type: str) -> None:
        _remote_fetch.write_cache(cache_file=cache_file, examples=examples, file_type=file_type)

    def _fetch_from_public_url(self, *, source: str, file_type: str) -> list[dict[str, str]]:
        return _remote_fetch.fetch_from_public_url(source=source, file_type=file_type)

    def _fetch_from_file(self, *, source: str, file_type: str) -> list[dict[str, str]]:
        return _remote_fetch.fetch_from_file(source=source, file_type=file_type)

    def _fetch_from_url(
        self,
        *,
        source: str,
        source_type: Literal["public_url", "file"] = "public_url",
        cache: bool = True,
    ) -> list[dict[str, str]]:
        """
        Fetch examples from a URL or local file with caching under ``seed-prompt-entries``.

        Dispatches through ``self._read_cache`` / ``self._write_cache`` /
        ``self._fetch_from_public_url`` / ``self._fetch_from_file`` so that
        subclasses and tests can override or mock any of those steps.

        Returns:
            The parsed examples.

        Raises:
            ValueError: If ``file_type`` inferred from ``source`` is not supported,
                or if ``source_type`` is not ``'public_url'`` or ``'file'``.
        """
        file_type = self._get_file_type(source=source)
        self._validate_file_type(file_type)

        cache_file = DB_DATA_PATH / _CACHE_SUBDIR / self._get_cache_file_name(source=source, file_type=file_type)

        if cache and cache_file.exists():
            return self._read_cache(cache_file=cache_file, file_type=file_type)

        if source_type == "public_url":
            examples = self._fetch_from_public_url(source=source, file_type=file_type)
        elif source_type == "file":
            examples = self._fetch_from_file(source=source, file_type=file_type)
        else:
            raise ValueError(f"Invalid source_type: {source_type}. Expected 'public_url' or 'file'.")

        if cache:
            self._write_cache(cache_file=cache_file, examples=examples, file_type=file_type)
        else:
            with tempfile.NamedTemporaryFile(
                delete=False, mode="w", suffix=f".{file_type}", encoding="utf-8"
            ) as temp_file:
                _remote_fetch.FILE_TYPE_HANDLERS[file_type]["write"](temp_file, examples)

        return examples

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

        def _load_dataset_sync() -> Any:
            """
            Run dataset loading synchronously in thread pool.

            Returns:
                Dataset: The loaded dataset from Hugging Face.
            """
            cache_dir = str(DB_DATA_PATH / "huggingface") if cache else None

            return load_dataset(
                dataset_name,
                config,
                split=split,
                cache_dir=cache_dir,
                download_mode=DownloadMode.REUSE_DATASET_IF_EXISTS,
                token=token,
                **kwargs,
            )

        try:
            return await asyncio.to_thread(_load_dataset_sync)
        except Exception as e:
            logger.error(f"Failed to load HuggingFace dataset {dataset_name}: {e}")
            raise

    async def _parse_metadata(self) -> Optional[SeedDatasetMetadata]:
        """
        Extract metadata from class attributes, wrap in sets, and format into SeedDatasetMetadata.

        Class attributes may be singular values (str, enum), lists, or sets.
        All are normalized into sets for the unified SeedDatasetMetadata schema.

        Returns:
            Optional[SeedDatasetMetadata]: Parsed metadata if available, otherwise None.
        """
        valid_fields = [f.name for f in fields(SeedDatasetMetadata)]

        provider_class = type(self)
        raw = {}
        for key in valid_fields:
            value = getattr(provider_class, key, None)
            if value is None:
                continue
            raw[key] = value

        if not raw:
            return None

        coerced = SeedDatasetMetadata._coerce_metadata_values(raw_metadata=raw)
        # Validation must happen after coercion because raw values are strings/lists,
        # not sets. _validate_singular_fields checks set cardinality (len > 1).
        result = SeedDatasetMetadata(**coerced)
        SeedDatasetMetadata._validate_singular_fields(metadata=result)
        return result
