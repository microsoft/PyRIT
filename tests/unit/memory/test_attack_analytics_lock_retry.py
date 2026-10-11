# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import TYPE_CHECKING, Any, TypeVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiosqlite import Connection as SQLiteConnection
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from pyrit.exceptions.analytics_exception import AnalyticsTimeoutException
from pyrit.memory import SQLiteMemory
from pyrit.memory.attack_analytics import AttackAnalyticsReader
from pyrit.memory.memory_models import Base
from pyrit.memory.query_control import QueryControl
from pyrit.models import (
    AttackAnalyticsDimension,
    AttackAnalyticsFacetQuery,
    AttackAnalyticsQuery,
    AttackAnalyticsResultsQuery,
    AttackOutcome,
    AttackResult,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from pathlib import Path

    from sqlalchemy import CursorResult, Executable, Result


_ResultT = TypeVar("_ResultT")


def _sqlite_error(code: int) -> OperationalError:
    original = sqlite3.OperationalError("database is locked")
    original.sqlite_errorcode = code
    return OperationalError("SELECT", {}, original)


@pytest.fixture
async def file_memory(tmp_path: Path) -> AsyncGenerator[SQLiteMemory, None]:
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=tmp_path / "analytics-retry.sqlite", skip_schema_migration=True)
    memory.results_path = str(tmp_path)
    try:
        async with memory._get_async_engine().begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await memory.add_attack_results_to_memory_async(
            attack_results=[
                AttackResult(
                    conversation_id="lock-retry",
                    objective="Retry a locked analytics read",
                    operation="lock-test",
                    outcome=AttackOutcome.SUCCESS,
                )
            ]
        )
        yield memory
    finally:
        await memory.dispose_engine_async()


async def _read_async(*, reader: AttackAnalyticsReader, method: str, control: QueryControl) -> None:
    if method in {"report", "matrix", "compact_report"}:
        report = await reader.report_async(
            query=AttackAnalyticsQuery(
                group_by=AttackAnalyticsDimension(name="targeted_harm_category"),
                compare_by=AttackAnalyticsDimension(name="operation") if method == "matrix" else None,
            ),
            control=control,
            use_compact_profiles=method == "compact_report",
        )
        assert report.counts == {"success": 1}
        assert len(report.results.items) == 1
        if method == "compact_report":
            assert report.profiles is not None
        elif method == "matrix":
            assert len(report.cells) == 1
    elif method == "results":
        results = await reader.results_async(query=AttackAnalyticsResultsQuery(), control=control)
        assert len(results.items) == 1
        assert results.items[0].operation == "lock-test"
    else:
        facets = await reader.facets_async(
            query=AttackAnalyticsFacetQuery(dimension=AttackAnalyticsDimension(name="operation")), control=control
        )
        assert [item.key.value for item in facets.items] == ["lock-test"]


async def test_sqlite_busy_retry_yields_between_attempts_async() -> None:
    operation = AsyncMock(side_effect=[_sqlite_error(sqlite3.SQLITE_BUSY), _sqlite_error(sqlite3.SQLITE_BUSY), 42])
    control = QueryControl(deadline=time.monotonic() + 10)
    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as sleep:
        assert await AttackAnalyticsReader._retry_sqlite_busy_async(operation=operation, control=control) == 42
    assert operation.await_count == 3
    assert sleep.await_count == 2
    assert all(call.args == (AttackAnalyticsReader.SQLITE_BUSY_RETRY_DELAY,) for call in sleep.await_args_list)


@pytest.mark.parametrize(
    "error",
    [
        _sqlite_error(sqlite3.SQLITE_LOCKED),
        _sqlite_error(sqlite3.SQLITE_BUSY_SNAPSHOT),
        _sqlite_error(sqlite3.SQLITE_BUSY_RECOVERY),
        _sqlite_error(sqlite3.SQLITE_ERROR),
        OperationalError("SELECT", {}, RuntimeError("database is locked")),
    ],
)
async def test_sqlite_busy_retry_preserves_other_errors_async(error: OperationalError) -> None:
    operation = AsyncMock(side_effect=error)
    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as sleep:
        with pytest.raises(OperationalError) as raised:
            await AttackAnalyticsReader._retry_sqlite_busy_async(
                operation=operation, control=QueryControl(deadline=time.monotonic() + 10)
            )
    assert raised.value is error
    operation.assert_awaited_once()
    sleep.assert_not_awaited()


async def test_sqlite_busy_retry_expiry_prevents_another_attempt_async() -> None:
    control = QueryControl(deadline=time.monotonic() + 10)
    operation = AsyncMock(side_effect=_sqlite_error(sqlite3.SQLITE_BUSY))

    async def expire_async(delay: float) -> None:
        assert 0 < delay <= control.remaining
        control.deadline = 0

    with patch.object(asyncio, "sleep", side_effect=expire_async):
        with pytest.raises(AnalyticsTimeoutException):
            await AttackAnalyticsReader._retry_sqlite_busy_async(operation=operation, control=control)
    operation.assert_awaited_once()


async def test_sqlite_busy_retry_caps_sleep_to_remaining_budget_async() -> None:
    operation = AsyncMock(side_effect=_sqlite_error(sqlite3.SQLITE_BUSY))
    control = MagicMock(spec=QueryControl)
    control.remaining = 0.002
    control.check.side_effect = [None, None, AnalyticsTimeoutException]
    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as sleep:
        with pytest.raises(AnalyticsTimeoutException):
            await AttackAnalyticsReader._retry_sqlite_busy_async(operation=operation, control=control)
    sleep.assert_awaited_once_with(0.002)
    operation.assert_awaited_once()


async def test_sqlite_read_completed_after_expiry_is_not_returned_async() -> None:
    control = QueryControl(deadline=time.monotonic() + 10)

    async def completed_async() -> int:
        control.deadline = 0
        return 42

    with pytest.raises(AnalyticsTimeoutException):
        await AttackAnalyticsReader._retry_sqlite_busy_async(operation=completed_async, control=control)


async def test_non_sqlite_execution_does_not_retry_sqlite_error_codes_async() -> None:
    error = _sqlite_error(sqlite3.SQLITE_BUSY)
    session = MagicMock(spec=AsyncSession)
    session.get_bind.return_value.dialect.name = "mssql"
    session.execute = AsyncMock(side_effect=error)
    reader = AttackAnalyticsReader(memory=MagicMock(spec=SQLiteMemory))
    with patch.object(reader, "_retry_sqlite_busy_async", new_callable=AsyncMock) as retry:
        with pytest.raises(OperationalError) as raised:
            await reader._execute_async(
                session=session,
                statement=text("SELECT 1"),
                control=QueryControl(deadline=time.monotonic() + 10),
            )
    assert raised.value is error
    session.execute.assert_awaited_once()
    retry.assert_not_awaited()


def _observe_busy(*, reader: AttackAnalyticsReader, observed: asyncio.Event) -> Callable[..., Awaitable[_ResultT]]:
    retry = reader._retry_sqlite_busy_async

    async def observe_async(*, operation: Callable[[], Awaitable[_ResultT]], control: QueryControl) -> _ResultT:
        async def observed_operation_async() -> _ResultT:
            try:
                return await operation()
            except OperationalError as error:
                if getattr(error.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY:
                    observed.set()
                raise

        return await retry(operation=observed_operation_async, control=control)

    return observe_async


@pytest.mark.parametrize("method", ["report", "matrix", "compact_report", "results", "facets"])
async def test_sqlite_read_succeeds_when_writer_releases_lock_async(*, file_memory: SQLiteMemory, method: str) -> None:
    reader = AttackAnalyticsReader(memory=file_memory)
    observed = asyncio.Event()
    engine = file_memory._get_async_engine()
    async with engine.connect() as writer:
        default_timeout = (await writer.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()
        await writer.exec_driver_sql("BEGIN EXCLUSIVE")
        with patch.object(
            reader, "_retry_sqlite_busy_async", side_effect=_observe_busy(reader=reader, observed=observed)
        ):
            task = asyncio.create_task(
                _read_async(reader=reader, method=method, control=QueryControl(deadline=time.monotonic() + 10))
            )
            try:
                await asyncio.wait_for(observed.wait(), timeout=5)
                assert not task.done()
                await writer.rollback()
                await asyncio.wait_for(task, timeout=5)
                async with engine.connect() as connection:
                    assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == default_timeout
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await writer.rollback()


@pytest.mark.parametrize("method", ["report", "matrix", "compact_report", "results", "facets"])
@pytest.mark.parametrize("cancel", ["control", "task"])
async def test_sqlite_lock_retry_cancellation_restores_connection_async(
    *, file_memory: SQLiteMemory, method: str, cancel: str
) -> None:
    reader = AttackAnalyticsReader(memory=file_memory)
    observed = asyncio.Event()
    control = QueryControl(deadline=time.monotonic() + 10)
    engine = file_memory._get_async_engine()
    async with engine.connect() as writer:
        default_timeout = (await writer.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()
        await writer.exec_driver_sql("BEGIN EXCLUSIVE")
        with patch.object(
            reader, "_retry_sqlite_busy_async", side_effect=_observe_busy(reader=reader, observed=observed)
        ):
            task = asyncio.create_task(_read_async(reader=reader, method=method, control=control))
            try:
                await asyncio.wait_for(observed.wait(), timeout=5)
                if cancel == "control":
                    control.cancel()
                    with pytest.raises(AnalyticsTimeoutException):
                        await asyncio.wait_for(task, timeout=1)
                else:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=1)
                async with engine.connect() as connection:
                    assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == default_timeout
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await writer.rollback()
    await _read_async(reader=reader, method=method, control=QueryControl(deadline=time.monotonic() + 10))


@pytest.mark.parametrize("statement", ["PRAGMA journal_mode", "BEGIN"])
async def test_sqlite_consistent_report_retries_setup_without_restarting_transaction_async(
    *, file_memory: SQLiteMemory, statement: str
) -> None:
    original = AsyncConnection.exec_driver_sql
    attempts = 0

    async def execute_async(self: AsyncConnection, sql: str) -> CursorResult[Any]:
        nonlocal attempts
        if sql == statement:
            attempts += 1
            if attempts == 1:
                raise _sqlite_error(sqlite3.SQLITE_BUSY)
        return await original(self, sql)

    with patch.object(AsyncConnection, "exec_driver_sql", execute_async):
        await _read_async(
            reader=AttackAnalyticsReader(memory=file_memory),
            method="report",
            control=QueryControl(deadline=time.monotonic() + 10),
        )
    assert attempts == 2


@pytest.mark.parametrize("timeout_ms", [0, 127, 5000])
async def test_sqlite_busy_timeout_is_zero_only_during_analytics_session_async(
    *, file_memory: SQLiteMemory, timeout_ms: int
) -> None:
    engine = file_memory._get_async_engine()
    async with engine.connect() as connection:
        await connection.exec_driver_sql(f"PRAGMA busy_timeout = {timeout_ms}")
    reader = AttackAnalyticsReader(memory=file_memory)
    async with reader._session_async(control=QueryControl(deadline=time.monotonic() + 10)) as (session, _, _):
        connection = await session.connection()
        assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == 0
    async with engine.connect() as connection:
        assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == timeout_ms


@pytest.mark.parametrize("cancel_in_body", [False, True])
async def test_sqlite_timeout_restoration_finishes_under_repeated_cancellation_async(
    *, file_memory: SQLiteMemory, cancel_in_body: bool
) -> None:
    reader = AttackAnalyticsReader(memory=file_memory)
    entered, restoring, release_restore = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finish_body = asyncio.Event()
    original_timeout = reader._sqlite_busy_timeout_async
    async with file_memory._get_async_engine().connect() as connection:
        default_timeout = (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()

    async def restore_async(*, driver: SQLiteConnection, timeout_ms: int) -> None:
        if timeout_ms:
            restoring.set()
            await release_restore.wait()
        await original_timeout(driver=driver, timeout_ms=timeout_ms)

    async def read_async() -> None:
        async with reader._session_async(control=QueryControl(deadline=time.monotonic() + 10)):
            entered.set()
            await finish_body.wait()

    with patch.object(reader, "_sqlite_busy_timeout_async", side_effect=restore_async):
        task = asyncio.create_task(read_async())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            if cancel_in_body:
                task.cancel("original cancellation")
            else:
                finish_body.set()
            await asyncio.wait_for(restoring.wait(), timeout=5)
            for index in range(2):
                task.cancel(
                    "original cancellation" if not cancel_in_body and index == 0 else "cancel restoration again"
                )
                await asyncio.sleep(0)
                assert not task.done()
            release_restore.set()
            with pytest.raises(asyncio.CancelledError, match="original cancellation"):
                await asyncio.wait_for(task, timeout=5)
        finally:
            release_restore.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with file_memory._get_async_engine().connect() as connection:
        assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == default_timeout


@pytest.mark.parametrize("failure", ["progress_handler", "busy_timeout"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_sqlite_reset_failure_discards_connection_and_preserves_cancellation_async(
    *, failure: str, cancelled: bool
) -> None:
    reader = AttackAnalyticsReader(memory=MagicMock(spec=SQLiteMemory))
    connection = MagicMock(spec=AsyncConnection)
    connection.invalidate = AsyncMock()
    driver = MagicMock(spec=SQLiteConnection)
    error = RuntimeError("connection reset failed")
    driver.set_progress_handler = AsyncMock(side_effect=error if failure == "progress_handler" else None)
    cancellation = asyncio.CancelledError("original cancellation") if cancelled else None
    with patch.object(
        reader,
        "_sqlite_busy_timeout_async",
        new_callable=AsyncMock,
        side_effect=error if failure == "busy_timeout" else None,
    ):
        with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError) as raised:
            await reader._restore_sqlite_settings_async(
                connection=connection, driver=driver, busy_timeout=5000, cancelled=cancellation
            )
    assert raised.value is (cancellation if cancelled else error)
    if cancelled:
        assert raised.value.__cause__ is error
    connection.invalidate.assert_awaited_once_with(error)


@pytest.mark.parametrize("method", ["report", "matrix", "compact_report", "results", "facets"])
async def test_every_analytics_projection_retries_busy_in_the_same_session_async(
    *, file_memory: SQLiteMemory, method: str
) -> None:
    original = AsyncSession.execute
    calls: dict[Executable, int] = {}
    sessions: set[AsyncSession] = set()

    async def execute_async(self: AsyncSession, statement: Executable) -> Result[Any]:
        sessions.add(self)
        calls[statement] = calls.get(statement, 0) + 1
        if calls[statement] == 1:
            raise _sqlite_error(sqlite3.SQLITE_BUSY)
        return await original(self, statement)

    with patch.object(AsyncSession, "execute", execute_async):
        await _read_async(
            reader=AttackAnalyticsReader(memory=file_memory),
            method=method,
            control=QueryControl(deadline=time.monotonic() + 10),
        )
    assert calls and all(count == 2 for count in calls.values())
    assert len(sessions) == 1
