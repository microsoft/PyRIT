# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Persist prompt generation records as JSON documents in the memory's results storage."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import ValidationError

from pyrit.backend.models.prompt_generations import PromptGenerationRecord
from pyrit.memory import DiskStorageIO

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface, StorageIO

logger = logging.getLogger(__name__)


class PromptGenerationStore:
    """
    Store generation records next to the memory's media output.

    Records live at ``<results_path>/prompt_generations/<id>.json``: the local data directory for
    SQLite memory and the results blob container for Azure SQL memory. One backend instance owns
    the records, so they are read once into an in-process cache that every write keeps current.
    """

    DIRECTORY_NAME: ClassVar[str] = "prompt_generations"

    def __init__(self, *, memory: MemoryInterface) -> None:
        """
        Bind the store to a memory's results storage.

        Args:
            memory (MemoryInterface): Memory whose ``results_path`` and ``results_storage_io`` hold the records.

        Raises:
            ValueError: If the memory has no results storage.
        """
        if memory.results_storage_io is None or not memory.results_path:
            raise ValueError("Prompt generation records need memory with results storage.")
        self._storage: StorageIO = memory.results_storage_io
        self._directory = f"{str(memory.results_path).rstrip('/')}/{self.DIRECTORY_NAME}"
        self._cache: dict[str, PromptGenerationRecord] | None = None

    @property
    def is_local(self) -> bool:
        """Whether records are stored on this machine's disk rather than shared storage."""
        return isinstance(self._storage, DiskStorageIO)

    async def save_async(self, record: PromptGenerationRecord) -> None:
        """
        Write a record, replacing any stored version. Local writes replace the file atomically.

        Args:
            record (PromptGenerationRecord): The record to write.
        """
        path = self._path(record.id)
        data = record.model_dump_json(indent=2).encode("utf-8")
        await self._storage.create_directory_if_not_exists_async(self._directory)
        if self.is_local:
            await asyncio.to_thread(_replace_file, Path(path), data)
        else:
            await self._storage.write_file_async(path, data)
        if self._cache is not None:
            self._cache[record.id] = record

    async def get_async(self, record_id: str) -> PromptGenerationRecord | None:
        """
        Read one record.

        Args:
            record_id (str): The record ID.

        Returns:
            PromptGenerationRecord | None: The record, or None if it doesn't exist.
        """
        return (await self._load_cache_async()).get(record_id)

    async def list_async(self) -> list[PromptGenerationRecord]:
        """
        Read every readable record, newest first. Unreadable documents are logged and skipped.

        Returns:
            list[PromptGenerationRecord]: Stored records.
        """
        records = (await self._load_cache_async()).values()
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    async def delete_async(self, record_id: str) -> None:
        """
        Delete one record. Deleting a missing record does nothing.

        Args:
            record_id (str): The record ID.
        """
        await self._storage.delete_file_async(self._path(record_id))
        if self._cache is not None:
            self._cache.pop(record_id, None)

    async def _load_cache_async(self) -> dict[str, PromptGenerationRecord]:
        if self._cache is None:
            self._cache = {record.id: record for record in await self._read_all_async()}
        return self._cache

    async def _read_all_async(self) -> list[PromptGenerationRecord]:
        records: list[PromptGenerationRecord] = []
        for path in await self._storage.list_files_async(self._directory):
            if not path.endswith(".json"):
                continue
            try:
                records.append(PromptGenerationRecord.model_validate_json(await self._storage.read_file_async(path)))
            except (ValidationError, ValueError) as error:
                logger.warning("Skipping unreadable prompt generation record %s: %s", path, error)
        return records

    def _path(self, record_id: str) -> str:
        if not record_id or Path(record_id).name != record_id or record_id.startswith("."):
            raise ValueError(f"Invalid prompt generation ID: {record_id!r}")
        return f"{self._directory}/{record_id}.json"


def _replace_file(path: Path, data: bytes) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
