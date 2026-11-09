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
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select, update

from pyrit.backend.models.datasets import DatasetUploadManifest
from pyrit.backend.services.dataset_upload_service import _validated_seeds
from pyrit.common import Singleton
from pyrit.memory import CentralMemory, SeedEntry, SQLiteMemory
from pyrit.memory.dataset_lifecycle import DatasetNameConflictError, entry_references_dataset_assets
from pyrit.memory.dataset_mutation import DatasetVersionConflictError, dataset_version, editable_seed
from pyrit.memory.memory_models import DatasetLifecycleEntry, PromptMemoryEntry
from pyrit.models import MessagePiece, SeedPrompt, SeedRecord, SeedUnion
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


async def _mutate_async(
    memory: SQLiteMemory, *, version: str, raw: list[dict[str, Any]], deletes: list[UUID] | None = None
) -> dict[str, Any]:
    operation_id = await memory.reserve_dataset_mutation_async(dataset_name="owned", paths=[])
    manifest = DatasetUploadManifest(
        name="owned",
        mode="update",
        expected_version=version,
        seeds=raw,
        delete_seed_ids=[str(value) for value in deletes or []],
    )

    def prepare(records: dict[UUID, SeedRecord]) -> list[SeedUnion]:
        return _validated_seeds(manifest=manifest, files={}, added_by="mutation", records=records)

    async def write_async(seeds: list[SeedUnion]) -> None:
        for seed in seeds:
            seed.value_sha256 = hashlib.sha256(seed.value.encode("utf-8")).hexdigest()

    try:
        result = await memory.commit_dataset_mutation_async(
            upload_id=operation_id,
            expected_version=version,
            delete_ids=deletes or [],
            prepare_seeds=prepare,
            write_media=write_async,
            max_records=10000,
            max_bytes=10000000,
        )
    except BaseException:
        await memory.cleanup_dataset_media_async(operation_id=operation_id, abandon_upload=True)
        raise
    finally:
        await memory.release_dataset_upload_async(operation_id)
    await memory.cleanup_dataset_media_async(operation_id=operation_id)
    return result


async def test_independent_file_connections_serialize_mutations_and_reject_stale_state(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    first, second = memories
    await first.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="old", data_type="text", dataset_name="owned")], added_by="sdk"
    )
    snapshot = await first.get_dataset_editing_snapshot_async("owned")
    results = await asyncio.gather(
        *(
            _mutate_async(memory, version=snapshot.version, raw=[{"data_type": "text", "value": "new"}])
            for memory in memories
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, (DatasetNameConflictError, DatasetVersionConflictError)) for result in results) == 1
    current = await second.get_dataset_editing_snapshot_async("owned")
    assert len(current.records) == 2
    with pytest.raises(DatasetVersionConflictError):
        await _mutate_async(first, version=snapshot.version, raw=[{"data_type": "text", "value": "retry"}])
    assert len((await second.get_dataset_editing_snapshot_async("owned")).records) == 2


async def test_editing_snapshot_waits_for_sdk_write_and_matches_its_committed_rows(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    first, second = memories
    await first.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="old", data_type="text", dataset_name="owned")], added_by="sdk"
    )
    original = await second.get_dataset_editing_snapshot_async("owned")
    identifier = original.records[0].id
    async with await first.get_session_async() as session:
        await session.execute(
            update(SeedEntry).where(SeedEntry.id == identifier).values(value="new", description="metadata")
        )
        waiting = asyncio.create_task(second.get_dataset_editing_snapshot_async("owned"))
        await session.commit()
    snapshot = await asyncio.wait_for(waiting, TEST_WATCHDOG_SECONDS)
    assert snapshot.records[0].value == "new" and snapshot.records[0].description == "metadata"
    assert snapshot.version != original.version
    assert snapshot.version == (await first.get_dataset_editing_snapshot_async("owned")).version
    with pytest.raises(DatasetVersionConflictError):
        await _mutate_async(second, version=original.version, raw=[{"data_type": "text", "value": "stale"}])


async def test_mutation_and_exact_dataset_delete_race_cannot_resurrect_population(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    first, second = memories
    await first.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="old", data_type="text", dataset_name="owned")], added_by="sdk"
    )
    snapshot = await first.get_dataset_editing_snapshot_async("owned")
    results = await asyncio.gather(
        _mutate_async(first, version=snapshot.version, raw=[{"data_type": "text", "value": "new"}]),
        second.delete_dataset_async(dataset_name="owned"),
        return_exceptions=True,
    )
    assert isinstance(results[1], UUID)
    assert isinstance(results[0], (dict, LookupError))
    await second.cleanup_dataset_media_async(operation_id=results[1])
    with pytest.raises(LookupError):
        await first.get_dataset_editing_snapshot_async("owned")


async def test_mutation_restart_recovers_only_new_staging_and_preserves_old_owned_assets(tmp_path: Path) -> None:
    db_path = tmp_path / "mutation.db"
    memory = await _open_memory_async(db_path)
    try:
        original = await _create_media_dataset_async(memory)
        staged = tmp_path / "new.bin"
        operation_id = await memory.reserve_dataset_mutation_async(dataset_name="owned", paths=[str(staged)])
        await memory.results_storage_io.write_file_async(staged, b"partial")
        # A second runtime may recover abandoned operations but must skip a native live owner.
        other = await _open_memory_async(db_path)
        try:
            await other.recover_dataset_cleanup_async()
            assert original.exists() and staged.exists()
        finally:
            await other.dispose_engine_async()
    finally:
        await memory.dispose_engine_async()
    recovered = await _open_memory_async(db_path)
    try:
        await recovered.recover_dataset_cleanup_async()
        assert original.exists() and not staged.exists()
        snapshot = await recovered.get_dataset_editing_snapshot_async("owned")
        assert len(snapshot.records) == 1 and snapshot.records[0].value == str(original)
        status = await recovered.cleanup_dataset_media_async(operation_id=operation_id)
        assert status["status"] == "completed"
    finally:
        await recovered.dispose_engine_async()


async def test_complete_state_token_accepts_equivalent_aba_but_not_membership(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    first, second = memories
    await first.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="old", data_type="text", dataset_name="owned")], added_by="sdk"
    )
    original = await first.get_dataset_editing_snapshot_async("owned")
    identifier = original.records[0].id
    async with await second.get_session_async() as session:
        await session.execute(update(SeedEntry).where(SeedEntry.id == identifier).values(description="temporary"))
        await session.commit()
        await session.execute(update(SeedEntry).where(SeedEntry.id == identifier).values(description=None))
        await session.commit()
    assert (await first.get_dataset_editing_snapshot_async("owned")).version == original.version
    response = await _mutate_async(
        first, version=original.version, raw=[editable_seed(record=original.records[0], version=original.version)]
    )
    assert response["updated"] == 1 and response["version"] == original.version
    async with await second.get_session_async() as session:
        await session.execute(update(SeedEntry).where(SeedEntry.id == identifier).values(prompt_group_id=uuid4()))
        await session.commit()
    with pytest.raises(DatasetVersionConflictError):
        await _mutate_async(first, version=original.version, raw=[{"data_type": "text", "value": "new"}])


def test_content_token_and_retention_handle_ignore_signed_display_url_parameters() -> None:
    entry = SeedEntry(
        entry=SeedPrompt(
            value="https://account.blob.core.windows.net/container/item.bin?sig=old",
            data_type="binary_path",
            dataset_name="owned",
            added_by="sdk",
            value_sha256="stored-bytes-hash",
        )
    )
    version = dataset_version(entries=[entry], storage=None)
    projected = editable_seed(record=entry.get_seed_record(), version=version)
    entry.value = "https://account.blob.core.windows.net/container/item.bin?sig=new&se=later"
    assert dataset_version(entries=[entry], storage=None) == version
    assert editable_seed(record=entry.get_seed_record(), version=version) == projected


async def test_restart_completes_committed_mutation_cleanup_without_removing_new_live_media(tmp_path: Path) -> None:
    db_path = tmp_path / "committed-mutation.db"
    memory = await _open_memory_async(db_path)
    try:
        old_path = await _create_media_dataset_async(memory)
        original = await memory.get_dataset_editing_snapshot_async("owned")
        new_path = tmp_path / "new-live.bin"
        operation_id = await memory.reserve_dataset_mutation_async(dataset_name="owned", paths=[str(new_path)])
        seed = SeedPrompt(
            value=str(new_path),
            data_type="binary_path",
            dataset_name="owned",
            added_by="mutation",
            value_sha256=hashlib.sha256(b"new").hexdigest(),
        )

        async def write_async(seeds: list[SeedUnion]) -> None:
            await memory.results_storage_io.write_file_async(new_path, b"new")

        result = await memory.commit_dataset_mutation_async(
            upload_id=operation_id,
            expected_version=original.version,
            delete_ids=[original.records[0].id],
            prepare_seeds=lambda records: [seed],
            write_media=write_async,
            max_records=10000,
            max_bytes=10000000,
        )
        await memory.release_dataset_upload_async(operation_id)
        with patch.object(memory.results_storage_io, "delete_file_async", side_effect=OSError("offline")):
            status = await memory.cleanup_dataset_media_async(operation_id=operation_id)
        assert status["status"] == "pending" and result["added"] == result["deleted"] == 1
        assert old_path.exists() and new_path.exists()
    finally:
        await memory.dispose_engine_async()
    recovered = await _open_memory_async(db_path)
    try:
        await recovered.recover_dataset_cleanup_async()
        assert not old_path.exists() and new_path.read_bytes() == b"new"
        snapshot = await recovered.get_dataset_editing_snapshot_async("owned")
        assert len(snapshot.records) == 1 and snapshot.records[0].id == seed.id
        assert snapshot.version == result["version"]
    finally:
        await recovered.dispose_engine_async()


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


def test_only_seeds_and_media_rows_reference_dataset_assets(tmp_path: Path) -> None:
    text = PromptMemoryEntry(entry=MessagePiece(conversation_id="c", role="user", original_value="hello"))
    media = PromptMemoryEntry(
        entry=MessagePiece(
            conversation_id="c",
            role="user",
            original_value=str(tmp_path / "image.png"),
            original_value_data_type="image_path",
        )
    )
    seed = SeedEntry(entry=SeedPrompt(value="text seed", dataset_name="any", added_by="test"))
    assert not entry_references_dataset_assets(text)
    assert entry_references_dataset_assets(media)
    assert entry_references_dataset_assets(seed)


async def test_text_message_writes_skip_the_lifecycle_lock_but_seeds_take_it(
    memories: tuple[SQLiteMemory, SQLiteMemory],
) -> None:
    memory = memories[0]
    async with await memory.get_session_async() as session:
        session.add(PromptMemoryEntry(entry=MessagePiece(conversation_id="c", role="user", original_value="hi")))
        await session.flush()
        assert not session.info.get("dataset_lifecycle_locked")
        session.add(SeedEntry(entry=SeedPrompt(value="seed", dataset_name="locked", added_by="test")))
        await session.flush()
        assert session.info.get("dataset_lifecycle_locked")
        await session.commit()
