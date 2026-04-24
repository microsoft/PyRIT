# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Provide functionality for storing and retrieving conversation history and embeddings.

This package defines the core `MemoryInterface` and concrete implementations for different storage backends.
"""

from pyrit.memory.azure_sql_memory import AzureSQLMemory
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_embedding import MemoryEmbedding
from pyrit.memory.memory_exporter import MemoryExporter
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.memory.memory_models import AttackResultEntry, EmbeddingDataEntry, PromptMemoryEntry, SeedEntry
from pyrit.memory.sqlite_memory import SQLiteMemory

__all__ = [
    "AttackResultEntry",
    "AzureSQLMemory",
    "CentralMemory",
    "SQLiteMemory",
    "EmbeddingDataEntry",
    "MemoryInterface",
    "MemoryEmbedding",
    "MemoryExporter",
    "PromptMemoryEntry",
    "SeedEntry",
]
