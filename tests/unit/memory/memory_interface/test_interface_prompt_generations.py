# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Prompt generation records: ordering, compare-and-set ownership, and the shared dataset name rule."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects import mssql
from sqlalchemy.ext.asyncio import AsyncSession

from pyrit.memory import SQLiteMemory
from pyrit.memory.dataset_lifecycle import DatasetNameConflictError, is_dataset_name_taken_async
from pyrit.memory.memory_models import DatasetLifecycleEntry, PromptGenerationEntry
from pyrit.models import (
    PromptGenerationProgress,
    PromptGenerationRecord,
    PromptGenerationStatus,
    SeedDataset,
    SeedObjective,
)

NOW = datetime(2026, 11, 18, 12, tzinfo=UTC)


def _generation(
    *,
    dataset_name: str = "generated",
    status: PromptGenerationStatus = PromptGenerationStatus.QUEUED,
    created_at: datetime = NOW,
    **overrides: Any,
) -> PromptGenerationRecord:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "generator": "target_objective",
        "dataset_name": dataset_name,
        "status": status,
        "request": {"instructions": "Ask for order details.", "count": 2},
        "targets": {"generation": "openai_chat"},
        "progress": PromptGenerationProgress(current_stage="objectives", items_total=2),
        "created_at": created_at,
        "updated_at": created_at,
    }
    values.update(overrides)
    return PromptGenerationRecord.model_validate(values)


def _changed(generation: PromptGenerationRecord, **changes: Any) -> PromptGenerationRecord:
    return PromptGenerationRecord.model_validate({**generation.model_dump(), **changes})


def _dataset(name: str, *values: str) -> SeedDataset:
    return SeedDataset(seeds=[SeedObjective(value=value, dataset_name=name) for value in values], dataset_name=name)


async def _stage_upload_async(memory: SQLiteMemory, name: str) -> None:
    async with await memory.get_session_async() as session:
        session.add(
            DatasetLifecycleEntry(id=uuid.uuid4(), dataset_name=name, kind="upload", status="staging", deleted_seeds=0)
        )
        await session.commit()


async def test_round_trip_keeps_every_field(sqlite_instance: SQLiteMemory) -> None:
    group_id = uuid.uuid4()
    generation = _generation(
        group_id=group_id,
        group_label="Suite",
        group_index=2,
        checkpoint={"objectives": ["a"], "conversation_id": "c"},
        summary={"group_count": 1},
        created_by="Ada",
        pause_for_review=True,
        started_at=NOW,
        execution_id=uuid.uuid4(),
    )
    await sqlite_instance.add_prompt_generations_async(generations=[generation])

    assert await sqlite_instance.get_prompt_generation_async(generation_id=generation.id) == generation
    async with await sqlite_instance.get_session_async() as session:
        entry = await session.scalar(select(PromptGenerationEntry))
        assert entry is not None and entry.pyrit_version


async def test_list_is_newest_first_then_by_id(sqlite_instance: SQLiteMemory) -> None:
    tied = sorted((uuid.uuid4() for _ in range(3)), reverse=True)
    older = _generation(dataset_name="older", created_at=NOW - timedelta(seconds=1))
    same_time = [
        _generation(id=generation_id, dataset_name=f"tied_{index}") for index, generation_id in enumerate(tied)
    ]
    await sqlite_instance.add_prompt_generations_async(generations=[older, *same_time])

    listed = await sqlite_instance.get_prompt_generations_async()
    assert [generation.id for generation in listed] == [*sorted(tied), older.id]


async def test_reading_an_unknown_generation_returns_none(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_prompt_generations_async(generations=[_generation()])
    assert await sqlite_instance.get_prompt_generation_async(generation_id=uuid.uuid4()) is None


async def test_batch_members_list_in_batch_order(sqlite_instance: SQLiteMemory) -> None:
    group_id = uuid.uuid4()
    # Descending IDs, so ordering by ID alone would list the last member first.
    ids = sorted((uuid.uuid4() for _ in range(3)), reverse=True)
    batch = [
        _generation(id=generation_id, dataset_name=f"member_{index}", group_id=group_id, group_index=index)
        for index, generation_id in enumerate(ids)
    ]
    await sqlite_instance.add_prompt_generations_async(generations=batch)

    expected = [generation.id for generation in batch]
    assert [generation.id for generation in await sqlite_instance.get_prompt_generations_async()] == expected


async def test_update_compares_status_and_execution(sqlite_instance: SQLiteMemory) -> None:
    generation = _generation()
    await sqlite_instance.add_prompt_generations_async(generations=[generation])
    owner = uuid.uuid4()
    running = _changed(generation, status=PromptGenerationStatus.RUNNING, execution_id=owner)

    claim = ["status", "execution_id"]
    assert not await sqlite_instance.try_update_prompt_generation_async(
        generation=running, expected_statuses=[PromptGenerationStatus.FAILED], fields=claim
    )
    assert await sqlite_instance.try_update_prompt_generation_async(
        generation=running, expected_statuses=[PromptGenerationStatus.QUEUED], fields=claim
    )
    checkpointed = _changed(running, checkpoint={"objectives": []})
    assert not await sqlite_instance.try_update_prompt_generation_async(
        generation=checkpointed,
        expected_statuses=[PromptGenerationStatus.RUNNING],
        expected_execution_id=uuid.uuid4(),
        fields=["checkpoint"],
    )
    assert await sqlite_instance.try_update_prompt_generation_async(
        generation=checkpointed,
        expected_statuses=[PromptGenerationStatus.RUNNING],
        expected_execution_id=owner,
        fields=["checkpoint"],
    )
    assert await sqlite_instance.get_prompt_generation_async(generation_id=generation.id) == checkpointed


@pytest.mark.parametrize(
    ("expected_statuses", "fields", "message"),
    [([], ["status"], "expected_statuses must not be empty"), ([PromptGenerationStatus.QUEUED], [], "fields")],
)
async def test_update_rejects_empty_statuses_or_fields(
    sqlite_instance: SQLiteMemory, expected_statuses: list[PromptGenerationStatus], fields: list[str], message: str
) -> None:
    generation = _generation()
    await sqlite_instance.add_prompt_generations_async(generations=[generation])
    with pytest.raises(ValueError, match=message):
        await sqlite_instance.try_update_prompt_generation_async(
            generation=_changed(generation, status=PromptGenerationStatus.RUNNING),
            expected_statuses=expected_statuses,
            fields=fields,
        )
    assert await sqlite_instance.get_prompt_generation_async(generation_id=generation.id) == generation


async def test_update_writes_only_the_given_fields(sqlite_instance: SQLiteMemory) -> None:
    generation = _generation(status=PromptGenerationStatus.AWAITING_REVIEW)
    await sqlite_instance.add_prompt_generations_async(generations=[generation])
    reviewed = _changed(generation, checkpoint={"objectives": ["reviewed"]})
    assert await sqlite_instance.try_update_prompt_generation_async(
        generation=reviewed, expected_statuses=[PromptGenerationStatus.AWAITING_REVIEW], fields=["checkpoint"]
    )

    # A transition built from the read before the review changes only its own fields.
    queued = _changed(generation, status=PromptGenerationStatus.QUEUED, review_completed=True)
    assert await sqlite_instance.try_update_prompt_generation_async(
        generation=queued,
        expected_statuses=[PromptGenerationStatus.AWAITING_REVIEW],
        fields=["status", "review_completed"],
    )
    stored = await sqlite_instance.get_prompt_generation_async(generation_id=generation.id)
    assert stored == _changed(reviewed, status=PromptGenerationStatus.QUEUED, review_completed=True)
    with pytest.raises(ValueError, match="can't be changed"):
        await sqlite_instance.try_update_prompt_generation_async(
            generation=queued, expected_statuses=[PromptGenerationStatus.QUEUED], fields=["created_at"]
        )


async def test_foreign_cancel_fences_the_previous_owner(sqlite_instance: SQLiteMemory) -> None:
    owner = uuid.uuid4()
    running = _generation(status=PromptGenerationStatus.RUNNING, execution_id=owner)
    await sqlite_instance.add_prompt_generations_async(generations=[running])
    cancelled = _changed(running, status=PromptGenerationStatus.CANCELLED, execution_id=None)
    assert await sqlite_instance.try_update_prompt_generation_async(
        generation=cancelled, expected_statuses=[PromptGenerationStatus.RUNNING], fields=["status", "execution_id"]
    )

    late_checkpoint = _changed(running, checkpoint={"objectives": ["late"]})
    assert not await sqlite_instance.try_update_prompt_generation_async(
        generation=late_checkpoint,
        expected_statuses=[PromptGenerationStatus.RUNNING],
        expected_execution_id=owner,
        fields=["checkpoint"],
    )
    assert (await sqlite_instance.get_prompt_generations_async())[0] == cancelled


@pytest.mark.parametrize("holder", ["stored", "staging", "generation"])
async def test_new_generations_reject_taken_names(sqlite_instance: SQLiteMemory, holder: str) -> None:
    if holder == "stored":
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value="existing", dataset_name="taken")], added_by="test"
        )
    elif holder == "staging":
        await _stage_upload_async(sqlite_instance, "taken")
    else:
        await sqlite_instance.add_prompt_generations_async(
            generations=[_generation(dataset_name="taken", status=PromptGenerationStatus.FAILED)]
        )
    batch = [_generation(dataset_name="free"), _generation(dataset_name="taken")]

    assert await sqlite_instance.is_dataset_name_available_async(dataset_name="free")
    assert not await sqlite_instance.is_dataset_name_available_async(dataset_name="taken")
    with pytest.raises(DatasetNameConflictError, match="named 'taken' already exists"):
        await sqlite_instance.add_prompt_generations_async(generations=batch)
    assert await sqlite_instance.get_prompt_generation_async(generation_id=batch[0].id) is None


async def test_name_check_uses_the_databases_comparison(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="existing", dataset_name="Foo")], added_by="test"
    )
    # SQLite compares names exactly, so only the stored spelling is taken.
    assert not await sqlite_instance.is_dataset_name_available_async(dataset_name="Foo")
    assert await sqlite_instance.is_dataset_name_available_async(dataset_name="foo")
    assert await sqlite_instance.is_dataset_name_available_async(dataset_name="FOO")


async def test_name_check_lets_the_database_decide_the_match() -> None:
    # A case- and width-insensitive collation can match "DATA" to a stored "ＤＡＴＡ"; no Python check may undo that.
    session = MagicMock(spec=AsyncSession)
    session.scalar = AsyncMock(return_value=1)

    assert await is_dataset_name_taken_async(session, dataset_name="DATA")
    # The database answers whether the name is taken; no stored spelling comes back to compare in Python.
    statement = str(session.scalar.call_args.args[0].compile(dialect=mssql.dialect()))
    assert statement.count("EXISTS") == 3
    assert "[SeedPromptEntries].dataset_name = " in statement
    assert "[PromptGenerationEntries].dataset_name = " in statement


async def test_batch_members_that_share_a_name_are_rejected_together(sqlite_instance: SQLiteMemory) -> None:
    batch = [_generation(dataset_name="same"), _generation(dataset_name="same"), _generation(dataset_name="other")]
    with pytest.raises(DatasetNameConflictError, match="named 'same' already exists"):
        await sqlite_instance.add_prompt_generations_async(generations=batch)
    assert await sqlite_instance.get_prompt_generations_async() == []


async def test_completed_and_cancelled_generations_release_their_names(sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_prompt_generations_async(
        generations=[
            _generation(dataset_name="done", status=PromptGenerationStatus.COMPLETED),
            _generation(dataset_name="stopped", status=PromptGenerationStatus.CANCELLED),
            _generation(dataset_name="paused", status=PromptGenerationStatus.AWAITING_REVIEW),
        ]
    )
    assert await sqlite_instance.is_dataset_name_available_async(dataset_name="done")
    assert await sqlite_instance.is_dataset_name_available_async(dataset_name="stopped")
    assert not await sqlite_instance.is_dataset_name_available_async(dataset_name="paused")


async def test_requeue_rechecks_the_name_excluding_its_own_hold(sqlite_instance: SQLiteMemory) -> None:
    cancelled = _generation(status=PromptGenerationStatus.CANCELLED)
    failed = _generation(dataset_name="held", status=PromptGenerationStatus.FAILED)
    await sqlite_instance.add_prompt_generations_async(generations=[cancelled, failed])
    assert await sqlite_instance.try_update_prompt_generation_async(
        generation=_changed(failed, status=PromptGenerationStatus.QUEUED),
        expected_statuses=[PromptGenerationStatus.FAILED],
        fields=["status"],
        reserve_dataset_name=True,
    )

    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="someone else's", dataset_name="generated")], added_by="test"
    )
    with pytest.raises(DatasetNameConflictError):
        await sqlite_instance.try_update_prompt_generation_async(
            generation=_changed(cancelled, status=PromptGenerationStatus.QUEUED),
            expected_statuses=[PromptGenerationStatus.CANCELLED],
            fields=["status"],
            reserve_dataset_name=True,
        )
    assert await sqlite_instance.get_prompt_generation_async(generation_id=cancelled.id) == cancelled


async def test_requeue_rejects_a_name_another_generation_took(sqlite_instance: SQLiteMemory) -> None:
    cancelled = _generation(status=PromptGenerationStatus.CANCELLED)
    await sqlite_instance.add_prompt_generations_async(generations=[cancelled])
    # The cancel released the name, so a new generation can take it.
    await sqlite_instance.add_prompt_generations_async(generations=[_generation()])

    with pytest.raises(DatasetNameConflictError, match="named 'generated' already exists"):
        await sqlite_instance.try_update_prompt_generation_async(
            generation=_changed(cancelled, status=PromptGenerationStatus.QUEUED),
            expected_statuses=[PromptGenerationStatus.CANCELLED],
            fields=["status"],
            reserve_dataset_name=True,
        )
    assert await sqlite_instance.get_prompt_generation_async(generation_id=cancelled.id) == cancelled


async def test_commit_saves_the_dataset_and_completes_the_record_together(sqlite_instance: SQLiteMemory) -> None:
    owner = uuid.uuid4()
    running = _generation(status=PromptGenerationStatus.RUNNING, execution_id=owner)
    await sqlite_instance.add_prompt_generations_async(generations=[running])
    completed = _changed(
        running, status=PromptGenerationStatus.COMPLETED, execution_id=None, summary={"group_count": 2}
    )
    dataset = _dataset("generated", "first", "first")

    assert not await sqlite_instance.commit_prompt_generation_dataset_async(
        generation=completed, expected_execution_id=uuid.uuid4(), dataset=dataset, added_by="Ada"
    )
    assert await sqlite_instance.get_seeds_async(dataset_name="generated") == []

    assert await sqlite_instance.commit_prompt_generation_dataset_async(
        generation=completed, expected_execution_id=owner, dataset=dataset, added_by="Ada"
    )
    seeds = await sqlite_instance.get_seeds_async(dataset_name="generated")
    assert [seed.value for seed in seeds] == ["first", "first"]
    assert {seed.added_by for seed in seeds} == {"Ada"}
    assert await sqlite_instance.get_prompt_generation_async(generation_id=running.id) == completed
    assert not await sqlite_instance.commit_prompt_generation_dataset_async(
        generation=completed, expected_execution_id=owner, dataset=dataset, added_by="Ada"
    )
    assert len(await sqlite_instance.get_seeds_async(dataset_name="generated")) == 2


async def test_commit_after_a_cancel_saves_nothing(sqlite_instance: SQLiteMemory) -> None:
    owner = uuid.uuid4()
    running = _generation(status=PromptGenerationStatus.RUNNING, execution_id=owner)
    await sqlite_instance.add_prompt_generations_async(generations=[running])
    cancelled = _changed(running, status=PromptGenerationStatus.CANCELLED, execution_id=None)
    assert await sqlite_instance.try_update_prompt_generation_async(
        generation=cancelled, expected_statuses=[PromptGenerationStatus.RUNNING], fields=["status", "execution_id"]
    )

    assert not await sqlite_instance.commit_prompt_generation_dataset_async(
        generation=_changed(running, status=PromptGenerationStatus.COMPLETED, execution_id=None),
        expected_execution_id=owner,
        dataset=_dataset("generated", "late"),
        added_by="Ada",
    )
    assert await sqlite_instance.get_seeds_async(dataset_name="generated") == []
    assert await sqlite_instance.get_prompt_generation_async(generation_id=running.id) == cancelled


@pytest.mark.parametrize("holder", ["stored", "staging", "generation"])
async def test_commit_rolls_back_when_the_name_was_taken(sqlite_instance: SQLiteMemory, holder: str) -> None:
    owner = uuid.uuid4()
    running = _generation(status=PromptGenerationStatus.RUNNING, execution_id=owner)
    await sqlite_instance.add_prompt_generations_async(generations=[running])
    if holder == "stored":
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value="someone else's", dataset_name="generated")], added_by="test"
        )
    elif holder == "staging":
        await _stage_upload_async(sqlite_instance, "generated")
    else:
        # Written directly, as only a broken name rule could let a second generation hold the name.
        async with await sqlite_instance.get_session_async() as session:
            session.add(PromptGenerationEntry(_generation(status=PromptGenerationStatus.FAILED)))
            await session.commit()

    with pytest.raises(DatasetNameConflictError):
        await sqlite_instance.commit_prompt_generation_dataset_async(
            generation=_changed(running, status=PromptGenerationStatus.COMPLETED, execution_id=None),
            expected_execution_id=owner,
            dataset=_dataset("generated", "mine"),
            added_by="Ada",
        )
    assert "mine" not in {seed.value for seed in await sqlite_instance.get_seeds_async(dataset_name="generated")}
    assert await sqlite_instance.get_prompt_generation_async(generation_id=running.id) == running


async def test_commit_rejects_seeds_from_another_dataset(sqlite_instance: SQLiteMemory) -> None:
    owner = uuid.uuid4()
    running = _generation(status=PromptGenerationStatus.RUNNING, execution_id=owner)
    await sqlite_instance.add_prompt_generations_async(generations=[running])
    with pytest.raises(ValueError, match="belong to the generation's dataset"):
        await sqlite_instance.commit_prompt_generation_dataset_async(
            generation=running, expected_execution_id=owner, dataset=_dataset("other", "x"), added_by="Ada"
        )
    assert await sqlite_instance.get_seeds_async(dataset_name="other") == []


async def test_delete_compares_status(sqlite_instance: SQLiteMemory) -> None:
    running = _generation(status=PromptGenerationStatus.RUNNING)
    await sqlite_instance.add_prompt_generations_async(generations=[running])
    inactive = [PromptGenerationStatus.FAILED, PromptGenerationStatus.COMPLETED]
    assert not await sqlite_instance.delete_prompt_generation_async(
        generation_id=running.id, expected_statuses=inactive
    )

    async with await sqlite_instance.get_session_async() as session:
        await session.execute(
            update(PromptGenerationEntry)
            .where(PromptGenerationEntry.id == running.id)
            .values(status=PromptGenerationStatus.FAILED.value)
        )
        await session.commit()
    assert await sqlite_instance.delete_prompt_generation_async(generation_id=running.id, expected_statuses=inactive)
    assert await sqlite_instance.get_prompt_generations_async() == []
    assert not await sqlite_instance.delete_prompt_generation_async(
        generation_id=running.id, expected_statuses=inactive
    )


async def test_upload_reservation_rejects_a_name_held_by_a_generation(sqlite_instance: SQLiteMemory) -> None:
    held = _generation(dataset_name="held", status=PromptGenerationStatus.INTERRUPTED)
    released = _generation(dataset_name="released", status=PromptGenerationStatus.COMPLETED)
    await sqlite_instance.add_prompt_generations_async(generations=[held, released])

    with pytest.raises(DatasetNameConflictError, match="prompt generation"):
        await sqlite_instance.reserve_dataset_upload_async(dataset_name="held", paths=[])
    upload_id = await sqlite_instance.reserve_dataset_upload_async(dataset_name="released", paths=[])
    await sqlite_instance.release_dataset_upload_async(upload_id)
