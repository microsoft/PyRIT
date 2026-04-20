# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared URL-fetch and disk-cache helpers used by both `_RemoteDatasetLoader`
(which produces `SeedDataset`) and `_RemoteQADatasetLoader` (which produces
`QuestionAnsweringDataset`). The callers supply their own `cache_subdir` so
each loader family keeps a separate on-disk cache namespace under
`DB_DATA_PATH`.
"""

import hashlib
import io
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional

import requests

from pyrit.common.csv_helper import read_csv, write_csv
from pyrit.common.json_helper import read_json, read_jsonl, write_json, write_jsonl
from pyrit.common.path import DB_DATA_PATH
from pyrit.common.text_helper import read_txt, write_txt

FILE_TYPE_HANDLERS: Dict[str, Dict[str, Callable]] = {
    "json": {"read": read_json, "write": write_json},
    "jsonl": {"read": read_jsonl, "write": write_jsonl},
    "csv": {"read": read_csv, "write": write_csv},
    "txt": {"read": read_txt, "write": write_txt},
}


def validate_file_type(file_type: str) -> None:
    """
    Validate that the file type is supported.

    Raises:
        ValueError: If the file_type is not in FILE_TYPE_HANDLERS.
    """
    if file_type not in FILE_TYPE_HANDLERS:
        valid_types = ", ".join(FILE_TYPE_HANDLERS.keys())
        raise ValueError(f"Invalid file_type. Expected one of: {valid_types}.")


def get_cache_file_name(*, source: str, file_type: str) -> str:
    """
    Generate a deterministic cache file name from an MD5 hash of the source.

    Returns:
        The cache file name, ``{md5(source)}.{file_type}``.
    """
    hash_source = hashlib.md5(source.encode("utf-8")).hexdigest()
    return f"{hash_source}.{file_type}"


def get_cache_file(*, source: str, file_type: str, cache_subdir: str) -> Path:
    """
    Resolve the cache path for a given source/file_type under ``cache_subdir``.

    Returns:
        The path ``DB_DATA_PATH/cache_subdir/{md5(source)}.{file_type}``.
    """
    return DB_DATA_PATH / cache_subdir / get_cache_file_name(source=source, file_type=file_type)


def read_cache(*, cache_file: Path, file_type: str) -> List[Dict[str, str]]:
    """
    Read cached examples from disk.

    Returns:
        The list of examples parsed from ``cache_file``.

    Raises:
        ValueError: If ``file_type`` is not supported.
    """
    validate_file_type(file_type)
    with cache_file.open("r", encoding="utf-8") as file:
        return FILE_TYPE_HANDLERS[file_type]["read"](file)


def write_cache(*, cache_file: Path, examples: List[Dict[str, str]], file_type: str) -> None:
    """
    Write examples to the cache file, creating parent directories as needed.

    Raises:
        ValueError: If ``file_type`` is not supported.
    """
    validate_file_type(file_type)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with cache_file.open("w", encoding="utf-8") as file:
        FILE_TYPE_HANDLERS[file_type]["write"](file, examples)


def fetch_from_public_url(*, source: str, file_type: str) -> List[Dict[str, str]]:
    """
    Fetch and parse examples from a public HTTP(S) URL.

    Returns:
        The parsed examples.

    Raises:
        ValueError: If ``file_type`` is not supported.
        Exception: If the HTTP response status is not 200.
    """
    validate_file_type(file_type)
    response = requests.get(source)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch examples from public URL. Status code: {response.status_code}")
    if file_type == "json":
        return FILE_TYPE_HANDLERS[file_type]["read"](io.StringIO(response.text))
    return FILE_TYPE_HANDLERS[file_type]["read"](io.StringIO("\n".join(response.text.splitlines())))


def fetch_from_file(*, source: str, file_type: str) -> List[Dict[str, str]]:
    """
    Read and parse examples from a local file.

    Returns:
        The parsed examples.

    Raises:
        ValueError: If ``file_type`` is not supported.
    """
    validate_file_type(file_type)
    with open(source, "r", encoding="utf-8") as file:
        return FILE_TYPE_HANDLERS[file_type]["read"](file)


def fetch_with_cache(
    *,
    source: str,
    cache_subdir: str,
    source_type: Literal["public_url", "file"] = "public_url",
    file_type: Optional[str] = None,
    cache: bool = True,
) -> List[Dict[str, str]]:
    """
    Fetch examples from a source with an MD5-keyed disk cache under `DB_DATA_PATH/<cache_subdir>`.

    Args:
        source: URL or local file path.
        cache_subdir: Subdirectory under ``DB_DATA_PATH`` for this caller's cache namespace.
        source_type: ``'public_url'`` or ``'file'``. Defaults to ``'public_url'``.
        file_type: Explicit file type; inferred from ``source``'s suffix if None.
        cache: If True, read from / write to the on-disk cache. Defaults to True.

    Returns:
        The parsed examples.

    Raises:
        ValueError: If the resolved ``file_type`` is not supported or
            ``source_type`` is not ``'public_url'`` or ``'file'``.
    """
    if file_type is None:
        file_type = source.rsplit(".", 1)[-1]
    validate_file_type(file_type)

    cache_file = get_cache_file(source=source, file_type=file_type, cache_subdir=cache_subdir)

    if cache and cache_file.exists():
        return read_cache(cache_file=cache_file, file_type=file_type)

    if source_type == "public_url":
        examples = fetch_from_public_url(source=source, file_type=file_type)
    elif source_type == "file":
        examples = fetch_from_file(source=source, file_type=file_type)
    else:
        raise ValueError(f"Invalid source_type: {source_type}. Expected 'public_url' or 'file'.")

    if cache:
        write_cache(cache_file=cache_file, examples=examples, file_type=file_type)
    else:
        with tempfile.NamedTemporaryFile(
            delete=False, mode="w", suffix=f".{file_type}", encoding="utf-8"
        ) as temp_file:
            FILE_TYPE_HANDLERS[file_type]["write"](temp_file, examples)

    return examples
