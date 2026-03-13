# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import hashlib
import io
import logging
import tempfile
from abc import ABC
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Optional, TextIO, cast

import requests
from datasets import DownloadMode, disable_progress_bars, load_dataset

from pyrit.common.csv_helper import read_csv, write_csv
from pyrit.common.json_helper import read_json, read_jsonl, write_json, write_jsonl
from pyrit.common.path import DB_DATA_PATH
from pyrit.common.text_helper import read_txt, write_txt
from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider

logger = logging.getLogger(__name__)

# Define the type for the file handlers
FileHandlerRead = Callable[[TextIO], list[dict[str, str]]]
FileHandlerWrite = Callable[[TextIO, list[dict[str, str]]], None]

FILE_TYPE_HANDLERS: dict[str, dict[str, Callable[..., Any]]] = {
    "json": {"read": read_json, "write": write_json},
    "jsonl": {"read": read_jsonl, "write": write_jsonl},
    "csv": {"read": read_csv, "write": write_csv},
    "txt": {"read": read_txt, "write": write_txt},
}


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

    def _get_cache_file_name(self, *, source: str, file_type: str) -> str:
        """
        Generate a cache file name based on the source URL and file type.

        Args:
            source: The source URL or file path.
            file_type: The file extension/type.

        Returns:
            str: The generated cache file name.
        """
        hash_source = hashlib.md5(source.encode("utf-8")).hexdigest()
        return f"{hash_source}.{file_type}"

    def _validate_file_type(self, file_type: str) -> None:
        """
        Validate that the file type is supported.

        Args:
            file_type (str): The file extension/type to validate.

        Raises:
            ValueError: If the file_type is invalid.
        """
        if file_type not in FILE_TYPE_HANDLERS:
            valid_types = ", ".join(FILE_TYPE_HANDLERS.keys())
            raise ValueError(f"Invalid file_type. Expected one of: {valid_types}.")

    def _read_cache(self, *, cache_file: Path, file_type: str) -> list[dict[str, str]]:
        """
        Read data from cache.

        Args:
            cache_file (Path): Path to the cache file.
            file_type (str): The file extension/type.

        Returns:
            List[Dict[str, str]]: The cached examples.

        Raises:
            ValueError: If the file_type is invalid.
        """
        self._validate_file_type(file_type)
        with cache_file.open("r", encoding="utf-8") as file:
            return cast("list[dict[str, str]]", FILE_TYPE_HANDLERS[file_type]["read"](file))

    def _write_cache(self, *, cache_file: Path, examples: list[dict[str, str]], file_type: str) -> None:
        """
        Write data to cache.

        Args:
            cache_file (Path): Path to the cache file.
            examples (List[Dict[str, str]]): The examples to cache.
            file_type (str): The file extension/type.

        Raises:
            ValueError: If the file_type is invalid.
        """
        self._validate_file_type(file_type)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with cache_file.open("w", encoding="utf-8") as file:
            FILE_TYPE_HANDLERS[file_type]["write"](file, examples)

    def _fetch_from_public_url(self, *, source: str, file_type: str) -> list[dict[str, str]]:
        """
        Fetch examples from a public URL.

        Args:
            source: The URL to fetch from.
            file_type: The file extension/type.

        Returns:
            List[Dict[str, str]]: The fetched examples.

        Raises:
            ValueError: If the file_type is invalid.
            Exception: If the request to fetch examples fails.
        """
        response = requests.get(source)
        if response.status_code == 200:
            if file_type in FILE_TYPE_HANDLERS:
                if file_type == "json":
                    return cast(
                        "list[dict[str, str]]", FILE_TYPE_HANDLERS[file_type]["read"](io.StringIO(response.text))
                    )
                return cast(
                    "list[dict[str, str]]",
                    FILE_TYPE_HANDLERS[file_type]["read"](io.StringIO("\n".join(response.text.splitlines()))),
                )
            valid_types = ", ".join(FILE_TYPE_HANDLERS.keys())
            raise ValueError(f"Invalid file_type. Expected one of: {valid_types}.")
        raise Exception(f"Failed to fetch examples from public URL. Status code: {response.status_code}")

    def _fetch_from_file(self, *, source: str, file_type: str) -> list[dict[str, str]]:
        """
        Fetch examples from a local file.

        Args:
            source: Path to the local file.
            file_type: The file extension/type.

        Returns:
            List[Dict[str, str]]: The fetched examples.

        Raises:
            ValueError: If the file_type is invalid.
        """
        with open(source, encoding="utf-8") as file:
            if file_type in FILE_TYPE_HANDLERS:
                return cast("list[dict[str, str]]", FILE_TYPE_HANDLERS[file_type]["read"](file))
            valid_types = ", ".join(FILE_TYPE_HANDLERS.keys())
            raise ValueError(f"Invalid file_type. Expected one of: {valid_types}.")

    def _fetch_from_url(
        self,
        *,
        source: str,
        source_type: Literal["public_url", "file"] = "public_url",
        cache: bool = True,
    ) -> list[dict[str, str]]:
        """
        Fetch examples from a specified source with caching support.

        Args:
            source: The source from which to fetch examples.
            source_type: The type of source ('public_url' or 'file').
            cache: Whether to cache the fetched examples. Defaults to True.

        Returns:
            List[Dict[str, str]]: A list of examples.

        Raises:
            ValueError: If the file_type is invalid.

        Example:
            >>> examples = self._fetch_from_url(
            ...     source='https://example.com/data.json',
            ...     source_type='public_url'
            ... )
        """
        file_type = source.split(".")[-1]
        if file_type not in FILE_TYPE_HANDLERS:
            valid_types = ", ".join(FILE_TYPE_HANDLERS.keys())
            raise ValueError(f"Invalid file_type. Expected one of: {valid_types}.")

        data_home = DB_DATA_PATH / "seed-prompt-entries"
        cache_file = data_home / self._get_cache_file_name(source=source, file_type=file_type)

        if cache and cache_file.exists():
            return self._read_cache(cache_file=cache_file, file_type=file_type)

        if source_type == "public_url":
            examples = self._fetch_from_public_url(source=source, file_type=file_type)
        elif source_type == "file":
            examples = self._fetch_from_file(source=source, file_type=file_type)

        if cache:
            self._write_cache(cache_file=cache_file, examples=examples, file_type=file_type)
        else:
            with tempfile.NamedTemporaryFile(
                delete=False, mode="w", suffix=f".{file_type}", encoding="utf-8"
            ) as temp_file:
                FILE_TYPE_HANDLERS[file_type]["write"](temp_file, examples)

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

            # Explicitly set download_mode to reuse cached data and never re-download
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
            # Run the synchronous load_dataset in a thread pool to avoid blocking the event loop
            return await asyncio.to_thread(_load_dataset_sync)
        except Exception as e:
            logger.error(f"Failed to load HuggingFace dataset {dataset_name}: {e}")
            raise
