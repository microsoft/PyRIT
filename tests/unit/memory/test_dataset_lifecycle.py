# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Independent file-backed connections exercise restart durability and real writer contention."""

import asyncio
import hashlib
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy import event, select, update

from pyrit.common.singleton import Singleton
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.dataset_lifecycle import DatasetNameConflictError
from pyrit.memory.memory_models import DatasetLifecycleEntry, SeedEntry
from pyrit.models import SeedPrompt
from pyrit.setup import SQLITE, initialize_pyrit_async
from pyrit.setup.pyrit_initializer import PyRITInitializer
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


async def test_initialization_recovers_abandoned_upload_before_seed_initializer(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    first, second = memories
    await first.reserve_dataset_upload_async(dataset_name="default", paths=[])
    await first.dispose_engine_async()
    initializer = MagicMock(spec=PyRITInitializer)

    async def insert_default_async() -> None:
        await second.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="default prompt", dataset_name="default")], added_by="initializer"
        )

    initializer.initialize_with_tracking_async.side_effect = insert_default_async
    with (
        patch.object(CentralMemory, "_memory_instance", second),
        patch.object(Singleton, "_instances", {SQLiteMemory: second}),
        patch("pyrit.setup.initialization.load_environment_async"),
    ):
        await initialize_pyrit_async(memory_db_type=SQLITE, initializers=[initializer])
    async with await second.get_session_async() as session:
        assert (await session.scalars(select(SeedEntry.dataset_name))).all() == ["default"]


@pytest.mark.parametrize("prior_work", [False, True])
async def test_rejected_core_update_cannot_be_caught_then_committed(
    memories: tuple[SQLiteMemory, SQLiteMemory], prior_work: bool
) -> None:
    first, second = memories
    seed = SeedPrompt(value="unchanged", dataset_name="existing", added_by="test")
    async with await first.get_session_async() as session:
        session.add(SeedEntry(entry=seed))
        await session.commit()
    await second.reserve_dataset_upload_async(dataset_name="reserved", paths=[])
    async with await first.get_session_async() as session:
        if prior_work:
            entry = await session.get(SeedEntry, seed.id)
            entry.value = "valid caller work"
        with pytest.raises(DatasetNameConflictError, match="reserved"):
            await session.execute(
                update(SeedEntry.__table__).where(SeedEntry.id == seed.id).values(dataset_name="reserved")
            )
        await session.commit()
    async with await second.get_session_async() as session:
        stored = await session.get(SeedEntry, seed.id)
        assert stored.dataset_name == "existing"
        assert stored.value == ("valid caller work" if prior_work else "unchanged")


async def test_collation_equivalent_stored_and_staging_names_conflict(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    first, second = memories
    seed = SeedPrompt(value="existing", dataset_name="Foo", added_by="test")
    async with await first.get_session_async() as session:
        session.add(SeedEntry(entry=seed))
        await session.commit()
    async with await second.get_session_async() as session:
        engine = session.bind.sync_engine

    def collated_comparison(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> tuple[str, Any]:
        # Emulate case-insensitive SQL equality without claiming a live SQL Server test.
        return (
            statement.replace(".dataset_name = ?", ".dataset_name COLLATE NOCASE = ?").replace(
                ".dataset_name IN (", ".dataset_name COLLATE NOCASE IN ("
            ),
            parameters,
        )

    event.listen(engine, "before_cursor_execute", collated_comparison, retval=True)
    try:
        with pytest.raises(DatasetNameConflictError):
            await second.reserve_dataset_upload_async(dataset_name="foo", paths=[])
        async with await second.get_session_async() as session:
            session.add(SeedEntry(entry=SeedPrompt(value="legacy alias", dataset_name="foo", added_by="sdk")))
            await session.commit()
        await second.delete_dataset_async(dataset_name="foo")
        async with await second.get_session_async() as session:
            assert (await session.scalars(select(SeedEntry.dataset_name))).all() == ["Foo"]
        await second.reserve_dataset_upload_async(dataset_name="Reserved", paths=[])
        with pytest.raises(DatasetNameConflictError):
            await second.reserve_dataset_upload_async(dataset_name="reserved", paths=[])
        async with await second.get_session_async() as session:
            session.add(SeedEntry(entry=SeedPrompt(value="sdk", dataset_name="reserved", added_by="sdk")))
            with pytest.raises(DatasetNameConflictError):
                await session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", collated_comparison)


async def test_recovery_does_not_consume_another_runtime_live_reservation(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    first, second = memories
    upload_id = await first.reserve_dataset_upload_async(dataset_name="live", paths=[])
    await second.recover_dataset_cleanup_async()
    seed = SeedPrompt(
        value="live",
        dataset_name="live",
        added_by="test",
        value_sha256=hashlib.sha256(b"live").hexdigest(),
    )

    async def write_async() -> None:
        return None

    await first.commit_dataset_upload_async(upload_id=upload_id, seeds=[seed], write_media=write_async)
    async with await second.get_session_async() as session:
        assert (await session.scalars(select(SeedEntry.dataset_name))).all() == ["live"]


async def test_recovery_proves_process_abandonment_before_reclaiming(
    memories: tuple[SQLiteMemory, SQLiteMemory], tmp_path: Path
) -> None:
    first, second = memories
    path = tmp_path / "process-partial.bin"
    script = """
import asyncio, sys
from pathlib import Path
from pyrit.memory import SQLiteMemory
async def main_async():
    memory = SQLiteMemory(db_path=sys.argv[1], silent=True)
    operation = await memory.reserve_dataset_upload_async(dataset_name="process-owned", paths=[sys.argv[2]])
    await asyncio.to_thread(Path(sys.argv[2]).write_bytes, b"partial")
    print("LEASE:" + str(operation), flush=True)
    await asyncio.Event().wait()
asyncio.run(main_async())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(first.db_path),
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), TEST_WATCHDOG_SECONDS)
            assert line, "Reservation process exited before announcing its lease."
            if line.startswith(b"LEASE:"):
                operation_id = UUID(line.decode().strip().removeprefix("LEASE:"))
                break
        await second.recover_dataset_cleanup_async()
        async with await second.get_session_async() as session:
            assert (await session.get(DatasetLifecycleEntry, operation_id)).status == "staging"
        assert path.read_bytes() == b"partial"
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.communicate(), TEST_WATCHDOG_SECONDS)
    await second.recover_dataset_cleanup_async()
    assert not path.exists()
    async with await second.get_session_async() as session:
        assert (await session.get(DatasetLifecycleEntry, operation_id)).status == "completed"
        assert not (await session.scalars(select(SeedEntry))).all()


async def test_upload_publication_is_fenced_after_ownership_is_lost(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    first, second = memories
    upload_id = await first.reserve_dataset_upload_async(dataset_name="fenced", paths=[])
    lease = first._dataset_upload_leases[upload_id]
    write = AsyncMock()
    with patch.object(lease, "active_async", return_value=False):
        with pytest.raises(DatasetNameConflictError, match="owner"):
            await first.commit_dataset_upload_async(
                upload_id=upload_id,
                seeds=[SeedPrompt(value="fenced", dataset_name="fenced", added_by="test")],
                write_media=write,
            )
    write.assert_not_awaited()
    await first.release_dataset_upload_async(upload_id)
    await second.recover_dataset_cleanup_async()
