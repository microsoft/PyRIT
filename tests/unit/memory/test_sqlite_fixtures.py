# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import gc
import re
import sqlite3
import time
import weakref
from collections.abc import Generator
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Engine, create_engine, event, func, inspect, select, text
from sqlalchemy.engine import AdaptedConnection
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool

from pyrit.common.singleton import Singleton
from pyrit.memory import CentralMemory, MemoryInterface, SQLiteMemory, migration
from pyrit.memory.memory_models import Base, PromptMemoryEntry
from pyrit.memory.memory_session import MemorySession
from pyrit.models import MessagePiece
from unit import conftest as unit_fixtures
from unit.async_utils import settle_tasks_async, wait_for_event_async
from unit.memory import test_conversation_branching
from unit.memory.memory_interface import test_interface_observations

_sqlite_instance = asynccontextmanager(unit_fixtures.sqlite_instance.__wrapped__)
_sqlite_template = contextmanager(unit_fixtures.sqlite_template.__wrapped__)


@pytest.mark.parametrize("operation", ["branch", "delete"])
async def test_concurrent_sqlite_tests_release_workers_after_watchdog_async(tmp_path: Path, operation: str) -> None:
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=tmp_path / "watchdog.db", skip_schema_migration=True)
    started, release = asyncio.Event(), asyncio.Event()
    before = asyncio.all_tasks()
    module = test_conversation_branching if operation == "branch" else test_interface_observations

    async def hold_write_async(driver: object) -> None:
        started.set()
        await release.wait()

    def pause_after_flush(session: MemorySession, context: object) -> None:
        connection = session.connection().connection.dbapi_connection
        assert isinstance(connection, AdaptedConnection)
        connection.run_async(hold_write_async)

    async def expire_async(*, future: asyncio.Future[object]) -> None:
        event.listen(MemorySession, "after_flush", pause_after_flush)
        try:
            await wait_for_event_async(event=started, tasks=[future])
            raise TimeoutError("forced watchdog expiry")
        finally:
            event.remove(MemorySession, "after_flush", pause_after_flush)

    try:
        await asyncio.to_thread(Base.metadata.create_all, memory.engine)
        with patch.object(module, "wait_for_completion_async", side_effect=expire_async):
            with pytest.raises(TimeoutError, match="forced watchdog expiry"):
                if operation == "branch":
                    test = test_conversation_branching.TestAtomicConversationBranching()
                    await test.test_concurrent_branch_creation_and_promotions_preserve_membership(file_memory=memory)
                else:
                    await test_interface_observations.test_concurrent_orm_score_deletions_clean_final_observation(
                        file_memory=memory
                    )
        assert not [task for task in asyncio.all_tasks() - before if not task.done()]
        async with memory._get_async_engine().connect() as connection:
            await connection.exec_driver_sql("PRAGMA busy_timeout = 0")
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            await connection.rollback()
    finally:
        release.set()
        remaining = asyncio.all_tasks() - before
        for task in remaining:
            task.cancel()
        await settle_tasks_async(*remaining)
        await memory.dispose_engine_async()


@pytest.fixture
def sqlite_connections() -> Generator[list[sqlite3.Connection], None, None]:
    connections: list[sqlite3.Connection] = []

    def record_connection(*, dbapi_connection: object, connection_record: ConnectionPoolEntry) -> None:
        if isinstance(dbapi_connection, sqlite3.Connection):
            connections.append(dbapi_connection)

    event.listen(Engine, "connect", record_connection, named=True)
    try:
        yield connections
    finally:
        event.remove(Engine, "connect", record_connection)


def _assert_closed(connections: list[sqlite3.Connection]) -> None:
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def _schema_snapshot(engine: Engine) -> dict[str, object]:
    inspector = inspect(engine)
    tables = {}
    for name in inspector.get_table_names():
        tables[name] = {
            "columns": [{**column, "type": str(column["type"])} for column in inspector.get_columns(name)],
            "primary_key": inspector.get_pk_constraint(name),
            "foreign_keys": sorted(
                inspector.get_foreign_keys(name),
                key=lambda constraint: (
                    constraint["constrained_columns"],
                    constraint["referred_table"],
                    constraint["referred_columns"],
                ),
            ),
            "indexes": sorted(inspector.get_indexes(name), key=lambda index: index["name"]),
            "unique_constraints": sorted(
                inspector.get_unique_constraints(name), key=lambda constraint: constraint["column_names"]
            ),
            "check_constraints": sorted(
                inspector.get_check_constraints(name), key=lambda constraint: constraint["sqltext"]
            ),
        }
    with engine.connect() as connection:
        revisions = connection.execute(text("SELECT version_num FROM pyrit_memory_alembic_version")).scalars().all()
    return {"tables": tables, "revisions": revisions}


async def test_sqlite_template_migrates_once_and_closes_connections(
    sqlite_connections: list[sqlite3.Connection],
) -> None:
    with (
        patch.object(migration, "run_schema_migrations", wraps=migration.run_schema_migrations) as migrate,
        patch.object(migration, "check_schema_migrations", wraps=migration.check_schema_migrations) as check,
        _sqlite_template() as template,
    ):
        for _ in range(3):
            async with _sqlite_instance(sqlite_template=template) as memory:
                assert (await memory.get_message_pieces_async()) == []
        migrate.assert_called_once()
        check.assert_called_once()
        assert template.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            template.execute("CREATE TABLE template_mutation (id INTEGER)")
    assert len(sqlite_connections) == 4
    _assert_closed(sqlite_connections)


@pytest.mark.parametrize("operation", ["run_schema_migrations", "check_schema_migrations"])
def test_sqlite_template_closes_connections_on_setup_failure(
    *, operation: str, sqlite_connections: list[sqlite3.Connection]
) -> None:
    def fail_after_connecting(*, engine: Engine) -> None:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE failed_setup (id INTEGER)"))
        raise RuntimeError("template setup failed")

    with patch.object(migration, operation, side_effect=fail_after_connecting):
        with pytest.raises(RuntimeError, match="template setup failed"):
            with _sqlite_template():
                pytest.fail("Template setup should fail before yielding")
    _assert_closed(sqlite_connections)


def test_sqlite_template_closes_connections_on_test_failure(sqlite_connections: list[sqlite3.Connection]) -> None:
    with pytest.raises(RuntimeError, match="test failed"):
        with _sqlite_template():
            raise RuntimeError("test failed")
    _assert_closed(sqlite_connections)


def test_sqlite_instance_copies_migrated_schema_and_revision(sqlite_instance: SQLiteMemory) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        migration.run_schema_migrations(engine=engine)
        migration.check_schema_migrations(engine=sqlite_instance.engine)
        assert _schema_snapshot(sqlite_instance.engine) == _schema_snapshot(engine)
    finally:
        engine.dispose()


async def test_sqlite_instance_isolates_rows_schema_and_results(
    *, sqlite_template: sqlite3.Connection, sqlite_connections: list[sqlite3.Connection]
) -> None:
    previous_memory = None
    previous_results_path = None
    for _ in range(3):
        async with _sqlite_instance(sqlite_template=sqlite_template) as memory:
            assert memory is not previous_memory
            assert memory.results_path != previous_results_path
            assert memory.memory_embedding is None
            assert isinstance(memory.engine.pool, StaticPool)
            assert CentralMemory.get_memory_instance() is memory
            assert SQLiteMemory() is memory
            inspector = inspect(memory.engine)
            assert "fixture_extra" not in inspector.get_table_names()
            assert inspector.get_temp_table_names() == []
            assert inspector.get_view_names() == []
            assert "ScoreEntries" in inspector.get_table_names()
            assert "fixture_column" not in {column["name"] for column in inspector.get_columns("SeedPromptEntries")}
            with memory.engine.begin() as connection:
                for table in Base.metadata.sorted_tables:
                    assert connection.execute(select(func.count()).select_from(table)).scalar_one() == 0
                assert (
                    connection.execute(text("SELECT version_num FROM pyrit_memory_alembic_version")).scalar_one()
                    != "fixture_revision"
                )
            memory._insert_entry(
                PromptMemoryEntry(
                    entry=MessagePiece(
                        role="user", original_value="fixture data", conversation_id="fixture-conversation"
                    )
                )
            )
            assert len(await memory.get_message_pieces_async()) == 1
            with memory.engine.begin() as connection:
                connection.execute(text("CREATE TABLE fixture_extra (id INTEGER)"))
                connection.execute(text("CREATE TEMP TABLE fixture_temp (id INTEGER)"))
                connection.execute(text("CREATE VIEW fixture_view AS SELECT id FROM fixture_extra"))
                connection.execute(text('ALTER TABLE "SeedPromptEntries" ADD COLUMN fixture_column TEXT'))
                connection.execute(text('DROP TABLE "ScoreEntries"'))
                connection.execute(text("UPDATE pyrit_memory_alembic_version SET version_num = 'fixture_revision'"))
            Path(memory.results_path, "fixture.txt").write_text("fixture data", encoding="utf-8")
            memory.memory_embedding = MagicMock()
            previous_memory = memory
            previous_results_path = memory.results_path
        assert not Path(previous_results_path).exists()
        _assert_closed(sqlite_connections)


@pytest.mark.parametrize("has_previous", [False, True])
async def test_sqlite_instance_restores_only_its_memory_registration(
    *, sqlite_template: sqlite3.Connection, has_previous: bool
) -> None:
    class UnrelatedSingleton(metaclass=Singleton):
        pass

    previous_memory = MagicMock(spec=SQLiteMemory)
    previous_central = MagicMock(spec=SQLiteMemory) if has_previous else None
    with patch.dict(Singleton._instances), patch.object(CentralMemory, "_memory_instance", previous_central):
        if has_previous:
            Singleton._instances[SQLiteMemory] = previous_memory
        else:
            Singleton._instances.pop(SQLiteMemory, None)
        async with _sqlite_instance(sqlite_template=sqlite_template) as memory:
            assert Singleton._instances[SQLiteMemory] is memory
            assert CentralMemory.get_memory_instance() is memory
            unrelated = UnrelatedSingleton()
        assert Singleton._instances.get(SQLiteMemory) is (previous_memory if has_previous else None)
        assert Singleton._instances[UnrelatedSingleton] is unrelated
        assert CentralMemory._memory_instance is previous_central


async def test_sqlite_instance_does_not_modify_an_existing_database(sqlite_template: sqlite3.Connection) -> None:
    async with _sqlite_instance(sqlite_template=sqlite_template) as previous:
        previous._insert_entry(
            PromptMemoryEntry(
                entry=MessagePiece(role="user", original_value="previous data", conversation_id="previous-conversation")
            )
        )
        async with _sqlite_instance(sqlite_template=sqlite_template) as current:
            assert current.engine is not previous.engine
            assert (await current.get_message_pieces_async()) == []
        assert SQLiteMemory() is previous
        assert CentralMemory.get_memory_instance() is previous
        assert (await previous.get_message_pieces_async())[0].original_value == "previous data"


@pytest.mark.parametrize("failure", ["backup", "test"])
async def test_sqlite_instance_cleans_up_on_failure(
    *, sqlite_template: sqlite3.Connection, sqlite_connections: list[sqlite3.Connection], failure: str
) -> None:
    with closing(sqlite3.connect(":memory:")) as closed_template:
        pass
    template = closed_template if failure == "backup" else sqlite_template
    expected_error = sqlite3.ProgrammingError if failure == "backup" else RuntimeError
    previous_memory = MagicMock(spec=SQLiteMemory)
    previous_central = MagicMock(spec=SQLiteMemory)
    with (
        patch.dict(Singleton._instances, {SQLiteMemory: previous_memory}),
        patch.object(CentralMemory, "_memory_instance", previous_central),
        patch.object(
            SQLiteMemory, "dispose_engine_async", autospec=True, side_effect=SQLiteMemory.dispose_engine_async
        ) as dispose,
    ):
        with pytest.raises(expected_error, match="closed|test failed"):
            async with _sqlite_instance(sqlite_template=template):
                raise RuntimeError("test failed")
        dispose.assert_called_once()
        memory = dispose.call_args.args[0]
        assert not Path(memory.results_path).exists()
        assert Singleton._instances[SQLiteMemory] is previous_memory
        assert CentralMemory.get_memory_instance() is previous_central
    _assert_closed(sqlite_connections)


def test_sqlite_instance_does_not_retain_process_exit_cleanup(sqlite_template: sqlite3.Connection) -> None:
    async def release_memory_async() -> weakref.ReferenceType[SQLiteMemory]:
        async with _sqlite_instance(sqlite_template=sqlite_template) as memory:
            assert memory.cleanup.__func__ is MemoryInterface.cleanup
            assert "cleanup" not in memory.__dict__
            return weakref.ref(memory)

    # Disposal's future can finish before its worker releases the bound method.
    with asyncio.Runner() as runner:
        memory_reference = runner.run(release_memory_async())
    gc.collect()
    assert memory_reference() is None


async def test_sqlite_instance_reset_still_runs_real_migrations(sqlite_instance: SQLiteMemory) -> None:
    before = _schema_snapshot(sqlite_instance.engine)
    sqlite_instance._insert_entry(
        PromptMemoryEntry(
            entry=MessagePiece(role="user", original_value="reset me", conversation_id="reset-conversation")
        )
    )
    with sqlite_instance.engine.begin() as connection:
        connection.execute(text('DROP TABLE "ScoreEntries"'))
    with patch.object(migration.command, "upgrade", wraps=migration.command.upgrade) as upgrade:
        await sqlite_instance.reset_database_async()
        upgrade.assert_called_once()
        assert upgrade.call_args.args[1] == "head"
    assert (await sqlite_instance.get_message_pieces_async()) == []
    assert _schema_snapshot(sqlite_instance.engine) == before


async def test_sqlite_instance_keeps_real_async_sessions_async(sqlite_instance: SQLiteMemory) -> None:
    with sqlite_instance.engine.begin() as connection:
        connection.execute(text("CREATE TABLE fixture_threads (value INTEGER)"))

    async def write_rows_async(worker: int) -> None:
        assert CentralMemory.get_memory_instance() is sqlite_instance
        for index in range(10):
            async with await sqlite_instance.get_session_async() as session:
                await session.execute(
                    text("INSERT INTO fixture_threads (value) VALUES (:value)"), {"value": worker * 10 + index}
                )
                await session.commit()

    await asyncio.gather(*(write_rows_async(worker) for worker in range(4)))
    async with await sqlite_instance.get_session_async() as session:
        assert (await session.execute(text("SELECT COUNT(DISTINCT value) FROM fixture_threads"))).scalar_one() == 40


async def test_file_sqlite_connections_skip_fsync_async(tmp_path: Path) -> None:
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=tmp_path / "fsync.db", skip_schema_migration=True)
    try:
        with memory.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 0
        async with await memory.get_session_async() as session:
            assert (await session.execute(text("PRAGMA synchronous"))).scalar_one() == 0
    finally:
        await memory.dispose_engine_async()


def test_file_sqlite_connect_does_not_wait_for_an_exclusive_lock(tmp_path: Path) -> None:
    database = tmp_path / "exclusive.db"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 30})
    try:
        with closing(sqlite3.connect(database, isolation_level=None)) as holder:
            holder.execute("CREATE TABLE lock_probe (id INTEGER)")
            holder.execute("BEGIN EXCLUSIVE")
            started = time.monotonic()
            with engine.connect() as connection:
                assert time.monotonic() - started < 15
                holder.execute("ROLLBACK")
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000
                assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
    finally:
        engine.dispose()


async def test_sqlite_busy_error_reports_lock_wait_async(tmp_path: Path) -> None:
    database = tmp_path / "busy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}", connect_args={"timeout": 0.05})
    try:
        with closing(sqlite3.connect(database, isolation_level=None)) as holder:
            holder.execute("CREATE TABLE lock_probe (id INTEGER)")
            holder.execute("BEGIN IMMEDIATE")
            async with engine.connect() as connection:
                with pytest.raises(OperationalError, match="database is locked") as locked:
                    await connection.exec_driver_sql("INSERT INTO lock_probe VALUES (1)")
        [note] = locked.value.__notes__
        waited = re.match(r"SQLite returned SQLITE_BUSY after (\d+\.\d\d) s\.", note)
        assert waited is not None
        assert float(waited.group(1)) >= 0.05
    finally:
        await engine.dispose()
