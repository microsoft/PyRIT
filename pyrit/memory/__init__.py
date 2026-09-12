# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Provide functionality for storing and retrieving conversation history and embeddings.

This package defines the core `MemoryInterface` and concrete implementations for different storage backends.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.memory.azure_sql_memory import AzureSQLMemory
    from pyrit.memory.central_memory import CentralMemory
    from pyrit.memory.memory_embedding import MemoryEmbedding
    from pyrit.memory.memory_interface import (
        AttackResultKeysetCursor,
        MemoryInterface,
        ScenarioHistoryAggregate,
        ScenarioHistoryKeysetCursor,
        ScenarioHistoryRunRecord,
    )
    from pyrit.memory.memory_models import AttackResultEntry, EmbeddingDataEntry, PromptMemoryEntry, SeedEntry
    from pyrit.memory.sqlite_memory import SQLiteMemory
    from pyrit.memory.storage import (
        AllowedCategories,
        AudioPathDataTypeSerializer,
        AzureBlobStorageIO,
        BinaryPathDataTypeSerializer,
        DataTypeSerializer,
        DiskStorageIO,
        ErrorDataTypeSerializer,
        ImagePathDataTypeSerializer,
        StorageIO,
        SupportedContentType,
        TextDataTypeSerializer,
        URLDataTypeSerializer,
        VideoPathDataTypeSerializer,
        data_serializer_factory,
        set_message_piece_sha256_async,
        set_seed_sha256_async,
    )

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AllowedCategories": "pyrit.memory.storage",
    "AttackResultEntry": "pyrit.memory.memory_models",
    "AttackResultKeysetCursor": "pyrit.memory.memory_interface",
    "AudioPathDataTypeSerializer": "pyrit.memory.storage",
    "AzureBlobStorageIO": "pyrit.memory.storage",
    "AzureSQLMemory": "pyrit.memory.azure_sql_memory",
    "BinaryPathDataTypeSerializer": "pyrit.memory.storage",
    "CentralMemory": "pyrit.memory.central_memory",
    "DataTypeSerializer": "pyrit.memory.storage",
    "data_serializer_factory": "pyrit.memory.storage",
    "DiskStorageIO": "pyrit.memory.storage",
    "EmbeddingDataEntry": "pyrit.memory.memory_models",
    "ErrorDataTypeSerializer": "pyrit.memory.storage",
    "ImagePathDataTypeSerializer": "pyrit.memory.storage",
    "MemoryInterface": "pyrit.memory.memory_interface",
    "MemoryEmbedding": "pyrit.memory.memory_embedding",
    "ScenarioHistoryKeysetCursor": "pyrit.memory.memory_interface",
    "ScenarioHistoryRunRecord": "pyrit.memory.memory_interface",
    "ScenarioHistoryAggregate": "pyrit.memory.memory_interface",
    "PromptMemoryEntry": "pyrit.memory.memory_models",
    "SeedEntry": "pyrit.memory.memory_models",
    "set_message_piece_sha256_async": "pyrit.memory.storage",
    "set_seed_sha256_async": "pyrit.memory.storage",
    "SQLiteMemory": "pyrit.memory.sqlite_memory",
    "StorageIO": "pyrit.memory.storage",
    "SupportedContentType": "pyrit.memory.storage",
    "TextDataTypeSerializer": "pyrit.memory.storage",
    "URLDataTypeSerializer": "pyrit.memory.storage",
    "VideoPathDataTypeSerializer": "pyrit.memory.storage",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
