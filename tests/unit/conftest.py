# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import sqlite3
import sys
import tempfile
import time
import weakref
from collections.abc import AsyncGenerator, Generator
from contextlib import closing, contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import ExceptionContext, ExecutionContext
from sqlalchemy.pool import ConnectionPoolEntry, Pool

from pyrit.common.path import DATASETS_PATH
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory, SQLiteMemory, migration
from pyrit.models import SeedDataset
from test_support.fixtures import use_sqlite_memory

# This limits retries and speeds up execution
os.environ["CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS"] = "5"
os.environ["RETRY_MAX_NUM_ATTEMPTS"] = "2"
os.environ["RETRY_WAIT_MIN_SECONDS"] = "0"
os.environ["RETRY_WAIT_MAX_SECONDS"] = "1"

_sqlite_statement_starts: weakref.WeakKeyDictionary[ExecutionContext, float] = weakref.WeakKeyDictionary()


@event.listens_for(Pool, "connect", named=True)
def _skip_sqlite_fsync(*, dbapi_connection: Any, connection_record: ConnectionPoolEntry, **kwargs: Any) -> None:
    """
    Turn off fsync when a unit test opens a SQLite connection.

    On loaded CI disks, each fsync can take seconds. The writer holds SQLite's lock during fsync, so
    concurrent writers can exceed the busy timeout. Locking and transactions do not change, and test
    databases do not need durability after an OS crash. The PRAGMA reads the schema, so it does not
    wait for an exclusive lock of another connection. In that case, the connection keeps fsync.
    """
    if not isinstance(connection_record.driver_connection, (sqlite3.Connection, aiosqlite.Connection)):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout")
        busy_timeout = cursor.fetchone()[0]
        cursor.execute("PRAGMA busy_timeout = 0")
        try:
            cursor.execute("PRAGMA synchronous = OFF")
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                raise
        finally:
            cursor.execute(f"PRAGMA busy_timeout = {busy_timeout}")
    finally:
        cursor.close()


@event.listens_for(Engine, "before_cursor_execute", named=True)
def _record_sqlite_statement_start(*, context: ExecutionContext | None, **kwargs: Any) -> None:
    if context is not None and context.dialect.name == "sqlite":
        _sqlite_statement_starts[context] = time.monotonic()


@event.listens_for(Engine, "handle_error")
def _note_sqlite_busy_wait(context: ExceptionContext) -> None:
    """Show how long a statement waited for the SQLite lock, to tell contention from an immediate conflict."""
    if getattr(context.original_exception, "sqlite_errorcode", None) != sqlite3.SQLITE_BUSY:
        return
    started = _sqlite_statement_starts.get(context.execution_context) if context.execution_context else None
    if started is not None and context.sqlalchemy_exception is not None:
        context.sqlalchemy_exception.add_note(
            f"SQLite returned SQLITE_BUSY after {time.monotonic() - started:.2f} s. A wait near the busy "
            "timeout means that another connection kept the lock. A wait near 0 s means that SQLite did not wait."
        )


@pytest.fixture
def block_dataset_provider_lookup() -> Generator[AsyncMock, None, None]:
    """Block live dataset lookup and discovery unless a test explicitly supplies fake providers."""
    message = (
        "Unit tests must not resolve live dataset providers. "
        "Use inline seeds, mock prepare_async, or patch lookup with fake providers in preparation tests."
    )
    with (
        patch.object(
            SeedDatasetProvider,
            "get_providers_by_name_async",
            new_callable=AsyncMock,
            side_effect=AssertionError(message),
        ) as lookup,
        patch.object(SeedDatasetProvider, "_materialize_builtin_providers", side_effect=AssertionError(message)),
    ):
        yield lookup


if sys.platform == "win32":
    from asyncio import AbstractEventLoop
    from collections.abc import Callable

    from test_support.event_loop import new_test_event_loop

    @pytest.fixture(scope="session")
    def _asyncio_loop_factory() -> Callable[[], AbstractEventLoop]:
        # pytest-asyncio 1.4's hook does not select factories for fixture-only runners.
        return new_test_event_loop


@contextmanager
def _sqlite_connection(engine: Engine) -> Generator[sqlite3.Connection, None, None]:
    with closing(engine.raw_connection()) as raw_connection:
        connection = raw_connection.driver_connection
        assert isinstance(connection, sqlite3.Connection)
        yield connection


@pytest.fixture(scope="session")
def sqlite_template() -> Generator[sqlite3.Connection, None, None]:
    """Migrate and validate a private, read-only template once per pytest worker."""
    engine = create_engine("sqlite:///:memory:")
    try:
        migration.run_schema_migrations(engine=engine)
        migration.check_schema_migrations(engine=engine)
        with _sqlite_connection(engine) as connection:
            connection.execute("PRAGMA query_only = ON")
            yield connection
    finally:
        engine.dispose()


@pytest.fixture
def garak_api_key_service_patterns() -> dict[str, str | None]:
    dataset = SeedDataset.from_yaml_file(
        DATASETS_PATH / "seed_datasets" / "local" / "garak" / "api_key_service_patterns.prompt"
    )
    patterns: dict[str, str | None] = {}
    for prompt in dataset.prompts:
        pattern_name = prompt.metadata["pattern_name"]
        assert pattern_name is None or isinstance(pattern_name, str)
        assert prompt.value not in patterns, f"Duplicate service: {prompt.value}"
        patterns[prompt.value] = pattern_name
    return patterns


@pytest.fixture
async def sqlite_instance(sqlite_template: sqlite3.Connection) -> AsyncGenerator[SQLiteMemory, None]:
    """Give each test its own database, result directory, and scoped memory instance."""
    with tempfile.TemporaryDirectory() as results_path:
        sqlite_memory = SQLiteMemory.__new__(SQLiteMemory)
        try:
            # Fixture teardown owns cleanup; process-exit hooks would retain every test's instance.
            with patch.object(sqlite_memory, "cleanup"):
                sqlite_memory.__init__(db_path=":memory:", skip_schema_migration=True)
            sqlite_memory.results_path = results_path
            sqlite_memory.disable_embedding()
            with _sqlite_connection(sqlite_memory.engine) as connection:
                sqlite_template.backup(connection)
            with use_sqlite_memory(sqlite_memory):
                yield sqlite_memory
        finally:
            await sqlite_memory.dispose_engine_async()


@pytest.fixture()
def patch_central_database(sqlite_instance):
    """Fixture to mock CentralMemory.get_memory_instance"""
    with patch.object(CentralMemory, "get_memory_instance", return_value=sqlite_instance) as sqlite_memory:
        yield sqlite_memory
