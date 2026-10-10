# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import sqlite3
import threading
import uuid
from collections.abc import AsyncGenerator, Awaitable, Iterable
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aiosqlite import Connection, Cursor
from sqlalchemy import text
from sqlalchemy.engine import Connection as SQLAlchemyConnection
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from pyrit.memory import SQLiteMemory
from pyrit.memory import sqlite_memory as sqlite_memory_module
from pyrit.memory.sqlite_memory import _CursorClosingSQLiteConnection
from pyrit.models import AttackOutcome, AttackResult, MessagePiece, MessageScorable, Score
from unit.async_utils import wait_for_completion_async
from unit.mocks import get_mock_scorer_identifier


@pytest.fixture(params=["in-memory", "file"])
async def sqlite_memory_async(
    *, sqlite_instance: SQLiteMemory, request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncGenerator[SQLiteMemory, None]:
    if request.param == "in-memory":
        yield sqlite_instance
        return
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=tmp_path / "cancellation.db", silent=True, _defer_initialization=True)
    memory.results_path = str(tmp_path)
    try:
        await memory.initialize_async()
        yield memory
    finally:
        await memory.dispose_engine_async()


@pytest.fixture
async def persistable_score_async(sqlite_memory_async: SQLiteMemory) -> Score:
    piece = MessagePiece(role="assistant", original_value="response", conversation_id="cancelled-score")
    await sqlite_memory_async.add_message_to_memory_async(request=piece.to_message())
    return Score(
        score_value="true",
        score_type="true_false",
        score_rationale="test score",
        scorer_class_identifier=get_mock_scorer_identifier(),
        scorable=MessageScorable(message_piece_ids=(piece.id,)),
    )


async def _assert_error_result_write_async(memory: SQLiteMemory) -> None:
    error_result = AttackResult(
        conversation_id="cancelled-score",
        objective="attack objective",
        outcome=AttackOutcome.ERROR,
        error_message="scoring failed",
        error_type="ValueError",
    )
    await memory.add_attack_results_to_memory_async(attack_results=[error_result])
    [stored] = await memory.get_attack_results_async(objective="attack objective")
    assert stored.attack_result_id == error_result.attack_result_id
    assert stored.error_message == "scoring failed"
    assert stored.outcome == AttackOutcome.ERROR
    await memory.add_message_to_memory_async(
        request=MessagePiece(
            role="user", original_value="after cleanup", conversation_id="cancelled-score"
        ).to_message()
    )


def test_native_connection_finalizes_retained_cursors_before_disconnect() -> None:
    uri = f"file:cursor-cleanup-{uuid.uuid4().hex}?mode=memory&cache=shared"
    with (
        closing(sqlite3.connect(uri, uri=True, factory=_CursorClosingSQLiteConnection)) as connection,
        closing(sqlite3.connect(uri, uri=True)) as writer,
    ):
        connection.executescript("CREATE TABLE Evidence (value INTEGER); INSERT INTO Evidence VALUES (1);")
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute("SELECT * FROM Evidence")
        connection.close()
        writer.execute("INSERT INTO Evidence VALUES (2)")
        writer.commit()
        assert writer.execute("SELECT COUNT(*) FROM Evidence").fetchone() == (2,)
        assert cursor.connection is connection


@pytest.mark.parametrize("additional_cancellations", [0, 1, 2])
async def test_cancelled_score_transaction_finishes_cleanup_before_return_async(
    *,
    sqlite_memory_async: SQLiteMemory,
    persistable_score_async: Score,
    additional_cancellations: int,
) -> None:
    started, release_read = asyncio.Event(), asyncio.Event()
    closing_connection, release_connection, connection_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    fetchall, close_connection = Cursor.fetchall, Connection.close
    selected_cursor: Cursor | None = None

    async def delayed_fetchall_async(cursor: Cursor) -> Iterable[Any]:
        nonlocal selected_cursor
        if not started.is_set():
            selected_cursor = cursor
            started.set()
            await release_read.wait()
        return await fetchall(cursor)

    async def delayed_connection_close_async(connection: Connection) -> None:
        closing_connection.set()
        await release_connection.wait()
        await close_connection(connection)
        connection_closed.set()

    with (
        patch.object(Cursor, "fetchall", new=delayed_fetchall_async),
        patch.object(Connection, "close", new=delayed_connection_close_async),
    ):
        task = asyncio.create_task(sqlite_memory_async.add_scores_to_memory_async(scores=[persistable_score_async]))
        try:
            await asyncio.wait_for(started.wait(), timeout=30)
            task.cancel("cancel score transaction")
            await asyncio.wait_for(closing_connection.wait(), timeout=30)
            for _ in range(additional_cancellations):
                task.cancel("cancel connection cleanup again")
                await asyncio.sleep(0)
                assert not task.done()
            assert not connection_closed.is_set()
            release_connection.set()
            with pytest.raises(asyncio.CancelledError, match="cancel score transaction") as raised:
                await wait_for_completion_async(future=task)
        finally:
            release_read.set()
            release_connection.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert selected_cursor is not None
    assert connection_closed.is_set()
    assert raised.value.args == ("cancel score transaction",)
    assert await sqlite_memory_async.get_scores_async() == []
    await _assert_error_result_write_async(sqlite_memory_async)


@pytest.mark.parametrize("additional_cancellations", [0, 2])
async def test_cancelled_session_finishes_rollback_before_return_async(
    *, sqlite_memory_async: SQLiteMemory, additional_cancellations: int
) -> None:
    async with await sqlite_memory_async.get_session_async() as session:
        await session.execute(text("CREATE TABLE CancellationProbe (value INTEGER)"))
        await session.commit()
    inserted, rolling_back, release_rollback, rolled_back = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    rollback = Connection.rollback

    async def delayed_rollback_async(connection: Connection) -> None:
        rolling_back.set()
        await release_rollback.wait()
        await rollback(connection)
        rolled_back.set()

    async def write_async() -> None:
        async with await sqlite_memory_async.get_session_async() as session:
            await session.execute(text("INSERT INTO CancellationProbe VALUES (1)"))
            inserted.set()
            await asyncio.Event().wait()
            await session.commit()

    with patch.object(Connection, "rollback", new=delayed_rollback_async):
        task = asyncio.create_task(write_async())
        try:
            await asyncio.wait_for(inserted.wait(), timeout=30)
            task.cancel("cancel transaction body")
            await asyncio.wait_for(rolling_back.wait(), timeout=30)
            for _ in range(additional_cancellations):
                task.cancel("cancel rollback again")
                await asyncio.sleep(0)
                assert not task.done()
            assert not rolled_back.is_set()
            release_rollback.set()
            with pytest.raises(asyncio.CancelledError, match="cancel transaction body"):
                await wait_for_completion_async(future=task)
        finally:
            release_rollback.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert rolled_back.is_set()
    async with await sqlite_memory_async.get_session_async() as session:
        assert await session.scalar(text("SELECT COUNT(*) FROM CancellationProbe")) == 0
        await session.execute(text("INSERT INTO CancellationProbe VALUES (2)"))
        await session.commit()
    await _assert_error_result_write_async(sqlite_memory_async)


@pytest.mark.parametrize(
    ("cancel_in_body", "additional_cancellations"),
    [(True, 0), (True, 2), (False, 0), (False, 2), (None, 0)],
    ids=["transaction-body", "transaction-body-repeated", "session-close", "session-close-repeated", "no-cancellation"],
)
async def test_failed_session_rollback_discards_connection_async(
    *, sqlite_memory_async: SQLiteMemory, cancel_in_body: bool | None, additional_cancellations: int
) -> None:
    async with await sqlite_memory_async.get_session_async() as session:
        await session.execute(text("CREATE TABLE CancellationProbe (value INTEGER)"))
        await session.commit()
    inserted, rolling_back, release_rollback, connection_closed = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    original = sqlite3.OperationalError("rollback failed")
    close = Connection.close
    driver: Connection | None = None
    original_cancellation: asyncio.CancelledError | None = None
    cancellation_message = "cancel transaction body" if cancel_in_body else "cancel session cleanup"

    async def failed_rollback_async(connection: Connection) -> None:
        rolling_back.set()
        await release_rollback.wait()
        raise original

    async def observed_close_async(connection: Connection) -> None:
        await close(connection)
        connection_closed.set()

    async def write_async() -> None:
        nonlocal driver, original_cancellation
        async with await sqlite_memory_async.get_session_async() as session:
            await session.execute(text("INSERT INTO CancellationProbe VALUES (1)"))
            connection = await session.connection()
            driver = (await connection.get_raw_connection()).driver_connection
            inserted.set()
            if cancel_in_body:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError as error:
                    original_cancellation = error
                    raise

    task = asyncio.create_task(write_async())
    try:
        with (
            patch.object(Connection, "rollback", new=failed_rollback_async),
            patch.object(Connection, "close", new=observed_close_async),
        ):
            await asyncio.wait_for(inserted.wait(), timeout=30)
            if cancel_in_body:
                task.cancel(cancellation_message)
            await asyncio.wait_for(rolling_back.wait(), timeout=30)
            if cancel_in_body is False:
                task.cancel(cancellation_message)
                await asyncio.sleep(0)
            for _ in range(additional_cancellations):
                task.cancel("cancel rollback again")
                await asyncio.sleep(0)
                assert not task.done()
            release_rollback.set()
            expected = OperationalError if cancel_in_body is None else asyncio.CancelledError
            message = "rollback failed" if cancel_in_body is None else cancellation_message
            with pytest.raises(expected, match=message) as raised:
                await wait_for_completion_async(future=task)

        assert connection_closed.is_set()
        error = raised.value if cancel_in_body is None else raised.value.__cause__
        assert isinstance(error, OperationalError)
        assert error.orig is original
        assert error.connection_invalidated
        if cancel_in_body:
            assert raised.value is original_cancellation
        async with await sqlite_memory_async.get_session_async() as session:
            assert await session.scalar(text("SELECT COUNT(*) FROM CancellationProbe")) == 0
        await _assert_error_result_write_async(sqlite_memory_async)
    finally:
        release_rollback.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if driver is not None:
            await driver.close()


async def test_cancelled_database_worker_finishes_before_return_async(
    *, sqlite_memory_async: SQLiteMemory, persistable_score_async: Score
) -> None:
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    closing, closed = asyncio.Event(), asyncio.Event()

    def wait_in_database() -> int:
        started.set()
        if not release.wait(timeout=60):
            raise RuntimeError("Database worker was not released")
        finished.set()
        return 1

    async with await sqlite_memory_async.get_session_async() as session:
        connection = await session.connection()
        await connection.run_sync(
            lambda sync_connection: sync_connection.connection.run_async(
                lambda driver: driver.create_function("wait_in_database", 0, wait_in_database)
            )
        )

    finish_cleanup = sqlite_memory_module._finish_sqlite_cleanup_async

    async def observed_cleanup_async(cleanup: Awaitable[None]) -> asyncio.CancelledError | None:
        closing.set()
        cancellation = await finish_cleanup(cleanup)
        closed.set()
        return cancellation

    async def read_async() -> None:
        async with await sqlite_memory_async.get_session_async() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            await session.execute(text('SELECT wait_in_database() FROM "PromptMemoryEntries"'))

    with patch.object(sqlite_memory_module, "_finish_sqlite_cleanup_async", new=observed_cleanup_async):
        task = asyncio.create_task(read_async())
        try:
            assert await asyncio.to_thread(started.wait, 30)
            task.cancel("cancel database worker")
            await asyncio.wait_for(closing.wait(), timeout=30)
            task.cancel("cancel worker cleanup again")
            await asyncio.sleep(0)
            assert not task.done()
            assert not finished.is_set()
            assert not closed.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError, match="cancel database worker"):
                await wait_for_completion_async(future=task)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert finished.is_set()
    assert closed.is_set()
    await _assert_error_result_write_async(sqlite_memory_async)


@pytest.mark.parametrize("additional_cancellations", [0, 1, 2])
@pytest.mark.parametrize("cancel_during_invalidation", [False, True], ids=["session-close", "invalidation"])
async def test_invalidation_failure_preserves_cancellation_and_cause_async(
    *,
    sqlite_memory_async: SQLiteMemory,
    persistable_score_async: Score,
    additional_cancellations: int,
    cancel_during_invalidation: bool,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    closing_cleanup, release_cleanup = asyncio.Event(), asyncio.Event()
    connection_closed, session_closed = asyncio.Event(), asyncio.Event()
    fetchall, invalidate = Cursor.fetchall, SQLAlchemyConnection.invalidate
    close_connection, close_session = Connection.close, AsyncSession.close
    original = ValueError("connection invalidation failed")
    original_cancellation: asyncio.CancelledError | None = None

    async def delayed_fetchall_async(cursor: Cursor) -> Iterable[Any]:
        nonlocal original_cancellation
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError as error:
            original_cancellation = error
            raise
        return await fetchall(cursor)

    def failing_invalidate(*args: Any, **kwargs: Any) -> None:
        invalidate(*args, **kwargs)
        raise original

    async def delayed_connection_close_async(connection: Connection) -> None:
        if cancel_during_invalidation:
            closing_cleanup.set()
            await release_cleanup.wait()
        await close_connection(connection)
        connection_closed.set()

    async def delayed_session_close_async(session: AsyncSession) -> None:
        if not cancel_during_invalidation:
            closing_cleanup.set()
            await release_cleanup.wait()
        await close_session(session)
        session_closed.set()

    with (
        patch.object(Cursor, "fetchall", new=delayed_fetchall_async),
        patch.object(SQLAlchemyConnection, "invalidate", autospec=True, side_effect=failing_invalidate),
        patch.object(Connection, "close", new=delayed_connection_close_async),
        patch.object(AsyncSession, "close", new=delayed_session_close_async),
    ):
        task = asyncio.create_task(sqlite_memory_async.add_scores_to_memory_async(scores=[persistable_score_async]))
        try:
            await asyncio.wait_for(started.wait(), timeout=30)
            task.cancel("cancel failed cleanup")
            await asyncio.wait_for(closing_cleanup.wait(), timeout=30)
            for _ in range(additional_cancellations):
                task.cancel("cancel cleanup again")
                await asyncio.sleep(0)
                assert not task.done()
            assert not session_closed.is_set()
            if cancel_during_invalidation:
                assert not connection_closed.is_set()
            release_cleanup.set()
            with pytest.raises(asyncio.CancelledError) as raised:
                await wait_for_completion_async(future=task)
        finally:
            release.set()
            release_cleanup.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert connection_closed.is_set()
    assert session_closed.is_set()
    assert original_cancellation is not None
    assert raised.value is original_cancellation
    assert raised.value.__cause__ is original
    assert await sqlite_memory_async.get_scores_async() == []
    await _assert_error_result_write_async(sqlite_memory_async)


async def test_sqlite_validation_error_preserves_original_exception_async(
    *, sqlite_memory_async: SQLiteMemory, persistable_score_async: Score
) -> None:
    original = ValueError("validation read failed")

    async def fail_fetchall_async(cursor: Cursor) -> Iterable[Any]:
        raise original

    with (
        patch.object(Cursor, "fetchall", new=fail_fetchall_async),
        pytest.raises(ValueError, match="validation read failed") as raised,
    ):
        await sqlite_memory_async.add_scores_to_memory_async(scores=[persistable_score_async])

    assert raised.value is original
    assert await sqlite_memory_async.get_scores_async() == []
    await _assert_error_result_write_async(sqlite_memory_async)
