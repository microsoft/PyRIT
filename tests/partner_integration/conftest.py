# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared fixtures for partner integration tests.

These tests validate that PyRIT's public APIs remain compatible with
partner packages that depend on them (e.g., azure-ai-evaluation[redteam]).
They do NOT require Azure credentials — all tests use in-memory fixtures.
"""

import asyncio
import os
import tempfile
from collections.abc import Generator
from unittest.mock import patch

import pytest
from sqlalchemy import inspect

from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.sqlite_memory import SQLiteMemory
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

# Limit retries for deterministic testing
os.environ["RETRY_MAX_NUM_ATTEMPTS"] = "3"
os.environ["RETRY_WAIT_MIN_SECONDS"] = "0"
os.environ["RETRY_WAIT_MAX_SECONDS"] = "1"


@pytest.fixture(scope="session", autouse=True)
def _initialize_pyrit():
    """Initialize PyRIT with in-memory database once per test session."""
    asyncio.run(initialize_pyrit_async(memory_db_type=IN_MEMORY))


@pytest.fixture(autouse=True)
def _restore_central_memory():
    """Save and restore CentralMemory singleton between tests.

    Prevents tests that call CentralMemory.set_memory_instance() from
    leaking state into subsequent tests.
    """
    previous = CentralMemory._memory_instance
    yield
    CentralMemory._memory_instance = previous


@pytest.fixture
def sqlite_instance() -> Generator[SQLiteMemory, None, None]:
    """Provide an in-memory SQLite database for partner integration tests."""
    sqlite_memory = SQLiteMemory(db_path=":memory:")
    temp_dir = tempfile.TemporaryDirectory()
    sqlite_memory.results_path = temp_dir.name
    sqlite_memory.disable_embedding()
    sqlite_memory.reset_database()

    inspector = inspect(sqlite_memory.engine)
    assert "PromptMemoryEntries" in inspector.get_table_names()
    assert "ScoreEntries" in inspector.get_table_names()
    assert "SeedPromptEntries" in inspector.get_table_names()

    CentralMemory.set_memory_instance(sqlite_memory)
    yield sqlite_memory
    temp_dir.cleanup()
    sqlite_memory.dispose_engine()


@pytest.fixture
def patch_central_database(sqlite_instance):
    """Mock CentralMemory.get_memory_instance for isolated tests."""
    with patch.object(CentralMemory, "get_memory_instance", return_value=sqlite_instance) as mock:
        yield mock
