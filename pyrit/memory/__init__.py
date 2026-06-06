# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Provide functionality for storing and retrieving conversation history and embeddings.

This package defines the core `MemoryInterface` and concrete implementations for different storage backends.
"""

from typing import Any

from pyrit.memory.azure_sql_memory import AzureSQLMemory
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_embedding import MemoryEmbedding
from pyrit.memory.memory_interface import MemoryInterface
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

__all__ = [
    "AllowedCategories",
    "AttackResultEntry",
    "AudioPathDataTypeSerializer",
    "AzureBlobStorageIO",
    "AzureSQLMemory",
    "BinaryPathDataTypeSerializer",
    "CentralMemory",
    "DataTypeSerializer",
    "data_serializer_factory",
    "DiskStorageIO",
    "EmbeddingDataEntry",
    "ErrorDataTypeSerializer",
    "ImagePathDataTypeSerializer",
    "MemoryInterface",
    "MemoryEmbedding",
    "MemoryExporter",
    "PromptMemoryEntry",
    "SeedEntry",
    "set_message_piece_sha256_async",
    "set_seed_sha256_async",
    "SQLiteMemory",
    "StorageIO",
    "SupportedContentType",
    "TextDataTypeSerializer",
    "URLDataTypeSerializer",
    "VideoPathDataTypeSerializer",
]


def __getattr__(name: str) -> Any:
    if name == "MemoryExporter":
        from pyrit.common.deprecation import print_deprecation_message
        from pyrit.memory.memory_exporter import MemoryExporter

        print_deprecation_message(
            old_item="pyrit.memory.MemoryExporter",
            new_item="the pyrit.output module or direct serialization",
            removed_in="0.15.0",
        )
        return MemoryExporter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
