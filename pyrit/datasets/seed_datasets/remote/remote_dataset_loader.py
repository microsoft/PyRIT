# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import contextlib
import hashlib
import io
import logging
import tempfile
import zipfile
from abc import ABC
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Literal, TextIO, cast
from urllib.parse import urlparse

import requests
from datasets import DownloadMode, disable_progress_bars, load_dataset

from pyrit.common.csv_helper import read_csv, write_csv
from pyrit.common.json_helper import read_json, read_jsonl, write_json, write_jsonl
from pyrit.common.path import DB_DATA_PATH
from pyrit.common.text_helper import read_txt, write_txt
from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.datasets.seed_datasets.seed_metadata import SeedDatasetMetadata
from pyrit.models.harm_category import standardize_harm_categories

logger = logging.getLogger(__name__)


# Define the type for the file handlers
FileHandlerRead = Callable[[TextIO], list[dict[str, str]]]
FileHandlerWrite = Callable[[TextIO, list[dict[str, str]]], None]


class _RemoteDatasetLoader(SeedDatasetProvider, ABC):
    """
    Abstract base class for loading remote datasets.

    Provides helper methods for fetching data from:
    - Public URLs (CSV, JSON, JSONL, TXT)
    - Local files
    - HuggingFace Hub

    Subclasses must implement:
    - fetch_dataset_async(): Fetch and return the dataset as a SeedDataset
    - dataset_name property: Human-readable name for the dataset
    """

    FILE_TYPE_HANDLERS: ClassVar[dict[str, dict[str, Callable[..., Any]]]] = {
        "json": {"read": read_json, "write": write_json},
        "jsonl": {"read": read_jsonl, "write": write_jsonl},
        "csv": {"read": read_csv, "write": write_csv},
        "txt": {"read": read_txt, "write": write_txt},
    }

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

    @staticmethod
    def _standardize_harm_categories(
        raw_categories: list[str] | str | None,
        *,
        alias_overrides: Mapping[str, object] | None = None,
    ) -> list[str]:
        """
        Standardize raw harm categories.

        Converts raw category string(s) to standardized HarmCategory enum names.

        Args:
            raw_categories: Raw category string(s) from the dataset
                           (e.g., "violence", "harmful"), or None.
            alias_overrides: Optional dataset-specific mapping that overrides alias
                resolution and can map one raw category to multiple canonical values.

        Returns:
            List of standardized HarmCategory enum names.
        """
        return standardize_harm_categories(raw_categories, alias_overrides=alias_overrides)

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
        if file_type not in self.FILE_TYPE_HANDLERS:
            valid_types = ", ".join(self.FILE_TYPE_HANDLERS.keys())
            raise ValueError(f"Invalid file_type. Expected one of: {valid_types}.")

    def _get_file_type(self, *, source: str) -> str:
        """
        Infer the source file type from a URL or local path.

        Query strings and fragments are ignored for URLs, and the result is
        normalized to lowercase so `.JSON` and `.json` are treated identically.

        Args:
            source (str): The URL or local file path to extract the file type from.

        Returns:
            str: The lowercase file extension without the leading dot.
        """
        parsed = urlparse(source)
        source_path = parsed.path if parsed.scheme else source
        suffix = Path(source_path).suffix
        return suffix.lstrip(".").lower()

    def _read_cache(self, *, cache_file: Path, file_type: str) -> list[dict[str, str]]:
        """
        Read data from cache.

        Args:
            cache_file (Path): Path to the cache file.
            file_type (str): The file extension/type.

        Returns:
            list[dict[str, str]]: The cached examples.

        Raises:
            ValueError: If the file_type is invalid.
        """
        self._validate_file_type(file_type)
        with cache_file.open("r", encoding="utf-8") as file:
            return cast("list[dict[str, str]]", self.FILE_TYPE_HANDLERS[file_type]["read"](file))

    def _write_cache(self, *, cache_file: Path, examples: list[dict[str, str]], file_type: str) -> None:
        """
        Write data to cache.

        Args:
            cache_file (Path): Path to the cache file.
            examples (list[dict[str, str]]): The examples to cache.
            file_type (str): The file extension/type.

        Raises:
            ValueError: If the file_type is invalid.
        """
        self._validate_file_type(file_type)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with cache_file.open("w", encoding="utf-8") as file:
            self.FILE_TYPE_HANDLERS[file_type]["write"](file, examples)

    def _fetch_from_public_url(self, *, source: str, file_type: str) -> list[dict[str, str]]:
        """
        Fetch examples from a public URL.

        Args:
            source: The URL to fetch from.
            file_type: The file extension/type.

        Returns:
            list[dict[str, str]]: The fetched examples.

        Raises:
            ValueError: If the file_type is invalid.
            Exception: If the request to fetch examples fails.
        """
        response = requests.get(source)
        if response.status_code == 200:
            if file_type in self.FILE_TYPE_HANDLERS:
                if file_type == "json":
                    return cast(
                        "list[dict[str, str]]", self.FILE_TYPE_HANDLERS[file_type]["read"](io.StringIO(response.text))
                    )
                return cast(
                    "list[dict[str, str]]",
                    self.FILE_TYPE_HANDLERS[file_type]["read"](io.StringIO("\n".join(response.text.splitlines()))),
                )
            valid_types = ", ".join(self.FILE_TYPE_HANDLERS.keys())
            raise ValueError(f"Invalid file_type. Expected one of: {valid_types}.")
        raise Exception(f"Failed to fetch examples from public URL. Status code: {response.status_code}")

    def _fetch_from_file(self, *, source: str, file_type: str) -> list[dict[str, str]]:
        """
        Fetch examples from a local file.

        Args:
            source: Path to the local file.
            file_type: The file extension/type.

        Returns:
            list[dict[str, str]]: The fetched examples.

        Raises:
            ValueError: If the file_type is invalid.
        """
        with open(source, encoding="utf-8") as file:
            if file_type in self.FILE_TYPE_HANDLERS:
                return cast("list[dict[str, str]]", self.FILE_TYPE_HANDLERS[file_type]["read"](file))
            valid_types = ", ".join(self.FILE_TYPE_HANDLERS.keys())
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
            list[dict[str, str]]: A list of examples.

        Raises:
            ValueError: If the file_type is invalid.

        Example:
            >>> examples = self._fetch_from_url(
            ...     source='https://example.com/data.json',
            ...     source_type='public_url'
            ... )
        """
        file_type = self._get_file_type(source=source)
        if file_type not in self.FILE_TYPE_HANDLERS:
            valid_types = ", ".join(self.FILE_TYPE_HANDLERS.keys())
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
                self.FILE_TYPE_HANDLERS[file_type]["write"](temp_file, examples)

        return examples

    async def _fetch_from_huggingface_async(
        self,
        *,
        dataset_name: str,
        config: str | None = None,
        split: str | None = None,
        cache: bool = True,
        token: str | None = None,
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
            >>> data = await self._fetch_from_huggingface_async(
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

            # Reuse cached data when caching is enabled; force a re-download otherwise so
            # cache=False genuinely picks up upstream edits instead of silently reusing the cache.
            download_mode = DownloadMode.REUSE_DATASET_IF_EXISTS if cache else DownloadMode.FORCE_REDOWNLOAD
            return load_dataset(
                dataset_name,
                config,
                split=split,
                cache_dir=cache_dir,
                download_mode=download_mode,
                token=token,
                **kwargs,
            )

        try:
            # Run the synchronous load_dataset in a thread pool to avoid blocking the event loop
            return await asyncio.to_thread(_load_dataset_sync)
        except Exception as e:
            logger.error(f"Failed to load HuggingFace dataset {dataset_name}: {e}")
            raise

    async def _parse_metadata_async(self) -> SeedDatasetMetadata | None:
        """
        Extract metadata from class attributes, wrap in sets, and format into SeedDatasetMetadata.

        Class attributes may be singular values (str, enum), lists, or sets.
        All are normalized into sets for the unified SeedDatasetMetadata schema.

        Returns:
            SeedDatasetMetadata | None: Parsed metadata if available, otherwise None.
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

    async def _fetch_zip_from_url_async(
        self,
        *,
        source: str,
        inner_files: list[str],
        cache: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Download a ZIP archive from ``source`` and return parsed contents of selected inner files.

        The downloaded zip is cached on disk (keyed by md5 of ``source``) when ``cache=True``,
        streamed in chunks to avoid double-buffering large archives in memory, and parsed in a
        worker thread so the event loop is never blocked. Each inner file is decoded with the
        handler in ``FILE_TYPE_HANDLERS`` matching its extension (json/jsonl/csv/txt).

        Args:
            source: HTTPS URL of the zip archive.
            inner_files: Paths inside the zip to extract (e.g. ``["MIC/train.jsonl"]``). Each
                path's extension must be one of ``FILE_TYPE_HANDLERS`` keys.
            cache: Whether to cache the downloaded zip on disk. Defaults to True.

        Returns:
            Mapping of each requested ``inner_files`` path to its parsed list of records.

        Raises:
            ValueError: If an ``inner_files`` extension is unsupported, or if a requested inner
                file is not present in the archive.
            Exception: If the HTTP request fails.
        """
        for inner in inner_files:
            self._validate_file_type(self._get_file_type(source=inner))

        cache_dir = DB_DATA_PATH / "seed-prompt-entries"
        cache_path = cache_dir / f"{hashlib.md5(source.encode('utf-8')).hexdigest()}.zip"

        def _download_and_parse() -> dict[str, list[dict[str, Any]]]:
            zip_path: Path
            temp_to_clean: Path | None = None
            if cache and cache_path.exists():
                zip_path = cache_path
            else:
                if cache:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    zip_path = cache_path
                else:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                        zip_path = Path(tmp.name)
                    temp_to_clean = zip_path

                logger.info(f"Downloading zip archive from {source}")
                with requests.get(source, stream=True) as response:
                    response.raise_for_status()
                    with zip_path.open("wb") as fh:
                        for chunk in response.iter_content(chunk_size=1 << 16):
                            if chunk:
                                fh.write(chunk)

            try:
                results: dict[str, list[dict[str, Any]]] = {}
                with zipfile.ZipFile(zip_path) as zf:
                    members = set(zf.namelist())
                    for inner in inner_files:
                        if inner not in members:
                            preview = ", ".join(sorted(members)[:10])
                            raise ValueError(
                                f"File '{inner}' not found in zip from {source}. Archive contains (preview): {preview}"
                            )
                        file_type = self._get_file_type(source=inner)
                        with zf.open(inner) as raw:
                            text = io.TextIOWrapper(raw, encoding="utf-8")
                            results[inner] = cast(
                                "list[dict[str, Any]]",
                                self.FILE_TYPE_HANDLERS[file_type]["read"](text),
                            )
                return results
            finally:
                if temp_to_clean is not None:
                    with contextlib.suppress(OSError):
                        temp_to_clean.unlink()

        return await asyncio.to_thread(_download_and_parse)
