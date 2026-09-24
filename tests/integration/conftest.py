# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import os
import tempfile
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack
from unittest.mock import patch

import pytest
from sqlalchemy import inspect

from pyrit.memory.azure_sql_memory import AzureSQLMemory
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.sqlite_memory import SQLiteMemory
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

# This limits retries to 10 attempts with a 1 second wait between retries
os.environ["RETRY_MAX_NUM_ATTEMPTS"] = "9"
os.environ["RETRY_WAIT_MIN_SECONDS"] = "0"
os.environ["RETRY_WAIT_MAX_SECONDS"] = "1"


async def _initialize_integration_memory_async() -> None:
    await initialize_pyrit_async(memory_db_type=IN_MEMORY)
    await CentralMemory.get_memory_instance().dispose_loop_resources_async()


asyncio.run(_initialize_integration_memory_async())


@pytest.fixture
async def azuresql_instance() -> AsyncGenerator[AzureSQLMemory, None]:
    connection_string = os.getenv("PYRIT_TEST_AZURE_SQL_CONNECTION_STRING")
    if not connection_string:
        pytest.skip("Set PYRIT_TEST_AZURE_SQL_CONNECTION_STRING to an isolated non-production database.")
    if connection_string == os.getenv(AzureSQLMemory.AZURE_SQL_DB_CONNECTION_STRING_PROD):
        pytest.fail("Azure SQL integration tests must not use the production database.")
    azuresql_memory = AzureSQLMemory.__new__(AzureSQLMemory)
    azuresql_memory.__init__(connection_string=connection_string, _defer_initialization=True)
    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(azuresql_memory.dispose_engine_async)
        await azuresql_memory.initialize_async()
        azuresql_memory.disable_embedding()
        async with await azuresql_memory.get_session_async() as session:
            connection = await session.connection()
            tables = await connection.run_sync(lambda sync_connection: inspect(sync_connection).get_table_names())
        assert {"PromptMemoryEntries", "EmbeddingData", "ScoreEntries", "SeedPromptEntries"} <= set(tables)
        cleanup.enter_context(patch.object(CentralMemory, "_memory_instance", azuresql_memory))
        yield azuresql_memory


def pytest_configure(config):
    # Let pytest know about your custom marker for help/usage info
    config.addinivalue_line("markers", "run_only_if_all_tests: skip test unless RUN_ALL_TESTS is set to true")


def pytest_collection_modifyitems(config, items):
    run_all = os.getenv("RUN_ALL_TESTS", "").lower() == "true"
    skip_marker = pytest.mark.skip(reason="RUN_ALL_TESTS is not set to true")
    for item in items:
        if "run_only_if_all_tests" in item.keywords and not run_all:
            item.add_marker(skip_marker)


@pytest.fixture
async def sqlite_instance() -> AsyncGenerator[SQLiteMemory, None]:
    sqlite_memory = SQLiteMemory.__new__(SQLiteMemory)
    sqlite_memory.__init__(db_path=":memory:", _defer_initialization=True)
    async with AsyncExitStack() as cleanup:
        sqlite_memory.results_path = cleanup.enter_context(tempfile.TemporaryDirectory())
        cleanup.push_async_callback(sqlite_memory.dispose_engine_async)
        sqlite_memory.disable_embedding()
        await sqlite_memory.initialize_async()
        async with await sqlite_memory.get_session_async() as session:
            connection = await session.connection()
            tables = await connection.run_sync(lambda sync_connection: inspect(sync_connection).get_table_names())
        assert {"PromptMemoryEntries", "EmbeddingData", "ScoreEntries", "SeedPromptEntries"} <= set(tables)
        cleanup.enter_context(patch.object(CentralMemory, "_memory_instance", sqlite_memory))
        yield sqlite_memory


@pytest.fixture()
def patch_central_database(sqlite_instance):
    """Fixture to mock CentralMemory.get_memory_instance"""
    with patch.object(CentralMemory, "get_memory_instance", return_value=sqlite_instance) as sqlite_memory:
        yield sqlite_memory
