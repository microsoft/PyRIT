# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Independent file-backed connections exercise restart durability and real writer contention."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import event, select, update

from pyrit.memory import SQLiteMemory
from pyrit.memory.dataset_lifecycle import DatasetNameConflictError
from pyrit.memory.memory_models import SeedEntry
from pyrit.models import SeedPrompt
from unit.async_utils import TEST_WATCHDOG_SECONDS


async def _open_memory_async(path: Path) -> SQLiteMemory:
    def create() -> SQLiteMemory:
        memory = SQLiteMemory.__new__(SQLiteMemory)
        with patch.object(memory, "cleanup"):
            memory.__init__(db_path=path, silent=True)
        memory.results_path = str(path.parent)
        return memory

    return await asyncio.to_thread(create)


@pytest.fixture
async def memories(tmp_path: Path) -> AsyncIterator[tuple[SQLiteMemory, SQLiteMemory]]:
    first = await _open_memory_async(tmp_path / "memory.db")
    second = await _open_memory_async(tmp_path / "memory.db")
    try:
        yield first, second
    finally:
        await first.dispose_engine_async()
        await second.dispose_engine_async()


async def _create_media_dataset_async(memory: SQLiteMemory) -> Path:
    path = Path(memory.results_path) / "media.bin"
    upload_id = await memory.reserve_dataset_upload_async(dataset_name="owned", paths=[str(path)])
    seed = SeedPrompt(
        value=str(path),
        data_type="binary_path",
        dataset_name="owned",
        added_by="test",
        value_sha256=hashlib.sha256(b"bytes").hexdigest(),
    )

    async def write_async() -> None:
        await memory.results_storage_io.write_file_async(path, b"bytes")

    await memory.commit_dataset_upload_async(upload_id=upload_id, seeds=[seed], write_media=write_async)
    return path


async def test_independent_connections_cannot_reserve_the_same_dataset(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    results = await asyncio.gather(
        *(memory.reserve_dataset_upload_async(dataset_name="same", paths=[]) for memory in memories),
        return_exceptions=True,
    )
    assert sum(isinstance(result, UUID) for result in results) == 1
    assert sum(isinstance(result, DatasetNameConflictError) for result in results) == 1
    # SDK writes participate in reservations too, not only API imports.
    async with await memories[1].get_session_async() as session:
        session.add(SeedEntry(entry=SeedPrompt(value="sdk", dataset_name="same", added_by="sdk")))
        with pytest.raises(DatasetNameConflictError, match="reserved"):
            await session.commit()


async def test_bulk_reference_validation_preserves_unflushed_orm_changes(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    memory = memories[0]
    first = SeedPrompt(value="original", dataset_name="first", added_by="test")
    second = SeedPrompt(value="other", dataset_name="second", added_by="test")
    async with await memory.get_session_async() as session:
        session.add_all([SeedEntry(entry=first), SeedEntry(entry=second)])
        await session.commit()
        entry = await session.get(SeedEntry, first.id)
        assert entry is not None
        entry.value = "pending"
        with session.no_autoflush:
            await session.execute(update(SeedEntry.__table__).where(SeedEntry.id == second.id).values(value="bulk"))
        assert entry.value == "pending"
        await session.commit()
    async with await memory.get_session_async() as session:
        assert dict((await session.execute(select(SeedEntry.dataset_name, SeedEntry.value))).all()) == {
            "first": "pending",
            "second": "bulk",
        }


async def test_deletion_waits_for_reference_writer_then_retains_shared_asset(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    first, second = memories
    path = await _create_media_dataset_async(first)
    attached, release, deletion_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    async with await second.get_session_async() as session:
        engine = session.bind.sync_engine

    def trace(*args: Any) -> None:
        if args[2] == "BEGIN IMMEDIATE":
            loop.call_soon_threadsafe(deletion_started.set)

    async def attach_async() -> None:
        async with await first.get_session_async() as session:
            session.add(
                SeedEntry(
                    entry=SeedPrompt(
                        value=str(path.parent / "unused" / ".." / path.name),
                        data_type="binary_path",
                        dataset_name="shared",
                        added_by="test",
                    )
                )
            )
            await session.flush()
            attached.set()
            await release.wait()
            await session.commit()

    writer = asyncio.create_task(attach_async())
    deletion = None
    event.listen(engine, "before_cursor_execute", trace)
    try:
        await asyncio.wait_for(attached.wait(), TEST_WATCHDOG_SECONDS)
        deletion = asyncio.create_task(second.delete_dataset_async(dataset_name="owned"))
        await asyncio.wait_for(deletion_started.wait(), TEST_WATCHDOG_SECONDS)
        assert not deletion.done()
    finally:
        release.set()
        await asyncio.wait_for(writer, TEST_WATCHDOG_SECONDS)
        event.remove(engine, "before_cursor_execute", trace)
    assert deletion is not None
    operation_id = await asyncio.wait_for(deletion, TEST_WATCHDOG_SECONDS)
    result = await second.cleanup_dataset_media_async(operation_id=operation_id)
    assert result["status"] == "completed"
    assert path.read_bytes() == b"bytes"


async def test_upload_and_pending_cleanup_survive_memory_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "persistent.db"
    memory = await _open_memory_async(db_path)
    try:
        path = await _create_media_dataset_async(memory)
    finally:
        await memory.dispose_engine_async()
    memory = await _open_memory_async(db_path)
    try:
        async with await memory.get_session_async() as session:
            stored = (await session.scalars(select(SeedEntry))).all()
            assert len(stored) == 1 and stored[0].value == str(path)
        deletion_id = await memory.delete_dataset_async(dataset_name="owned")
        assert path.exists()
    finally:
        await memory.dispose_engine_async()
    memory = await _open_memory_async(db_path)
    try:
        await memory.recover_dataset_cleanup_async()
        assert not path.exists()
        assert (await memory.cleanup_dataset_media_async(operation_id=deletion_id))["status"] == "completed"
        abandoned = tmp_path / "abandoned.bin"
        await memory.reserve_dataset_upload_async(dataset_name="abandoned", paths=[str(abandoned)])
        abandoned.write_bytes(b"partial")
    finally:
        await memory.dispose_engine_async()
    memory = await _open_memory_async(db_path)
    try:
        await memory.recover_dataset_cleanup_async()
        assert not abandoned.exists()
        async with await memory.get_session_async() as session:
            assert not (await session.scalars(select(SeedEntry))).all()
    finally:
        await memory.dispose_engine_async()
