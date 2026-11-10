# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the prompt generation job service, with real generators and a mock target."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.models.prompt_generations import (
    PromptGenerationBatchCreateRequest,
    PromptGenerationCreateRequest,
    PromptGenerationProgress,
    PromptGenerationRecord,
    PromptGenerationReviewUpdate,
    PromptGenerationStatus,
)
from pyrit.backend.services.prompt_generation_service import (
    PromptGenerationConflictError,
    PromptGenerationNotFoundError,
    PromptGenerationService,
)
from pyrit.backend.services.prompt_generation_store import PromptGenerationStore
from pyrit.memory import DiskStorageIO, MemoryInterface, SQLiteMemory
from pyrit.models import AssertGenerationStage, AssertGroupMetadata, Message, MessagePiece, SeedObjective
from pyrit.prompt_target import PromptTarget
from unit.assert_mocks import assert_generation_response_async, make_assert_config, make_assert_generation_target
from unit.async_utils import settle_tasks_async, wait_for_completion_async, wait_for_event_async

TARGET_NAME = "generation_target"


@pytest.fixture
def no_retry_wait() -> Iterator[None]:
    with patch.dict(
        "os.environ",
        {"RETRY_MAX_NUM_ATTEMPTS": "2", "RETRY_WAIT_MIN_SECONDS": "0", "RETRY_WAIT_MAX_SECONDS": "0"},
    ):
        yield


def _local_store(directory: Path) -> PromptGenerationStore:
    memory = MagicMock(spec=MemoryInterface)
    memory.results_path = str(directory)
    memory.results_storage_io = DiskStorageIO()
    return PromptGenerationStore(memory=memory)


@pytest.fixture
def store(tmp_path: Path) -> PromptGenerationStore:
    return _local_store(tmp_path)


@pytest.fixture
def target(patch_central_database: MagicMock) -> PromptTarget:
    return make_assert_generation_target()


@pytest.fixture
async def service(
    sqlite_instance: SQLiteMemory, store: PromptGenerationStore, target: PromptTarget, no_retry_wait: None
) -> AsyncIterator[PromptGenerationService]:
    generation_service = PromptGenerationService(
        memory=sqlite_instance, store=store, target_lookup=lambda name: target if name == TARGET_NAME else None
    )
    yield generation_service
    await generation_service.shutdown_async()


def _assert_request(**overrides: Any) -> PromptGenerationCreateRequest:
    values: dict[str, Any] = {
        "generator": "assert",
        "dataset_name": "support_policy",
        "request": make_assert_config().model_dump(mode="json"),
        "targets": {"generation": TARGET_NAME},
    }
    values.update(overrides)
    return PromptGenerationCreateRequest.model_validate(values)


def _record(*, status: PromptGenerationStatus, dataset_name: str = "support_policy") -> PromptGenerationRecord:
    now = datetime.now(UTC)
    return PromptGenerationRecord(
        id="00000000-0000-4000-8000-000000000001",
        generator="assert",
        dataset_name=dataset_name,
        status=status,
        request=make_assert_config().model_dump(mode="json"),
        created_at=now,
        updated_at=now,
    )


async def _settle_async(service: PromptGenerationService) -> None:
    await settle_tasks_async(*list(service._tasks.values()))


def _respond_with(target: PromptTarget, side_effect: Callable[..., Any] = assert_generation_response_async) -> Any:
    return patch.object(target, "_send_prompt_to_target_async", side_effect=side_effect)


def _stage_of(normalized_conversation: list[Message]) -> str:
    return str(normalized_conversation[-1].get_piece().prompt_metadata["assert_stage"])


async def test_assert_generation_runs_and_saves_dataset_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    with _respond_with(target):
        created = await service.create_async(request=_assert_request(), created_by="Ada")
        assert created.status == PromptGenerationStatus.QUEUED
        await _settle_async(service)

    record = await service.get_async(created.id)
    assert record.status == PromptGenerationStatus.COMPLETED
    assert record.progress.items_completed == record.progress.items_total == 6
    assert record.summary is not None
    assert record.summary["group_count"] == 6
    assert record.completed_at is not None
    objectives = [
        seed
        for seed in await sqlite_instance.get_seeds_async(dataset_name="support_policy")
        if isinstance(seed, SeedObjective)
    ]
    assert len(objectives) == 6
    assert {seed.added_by for seed in objectives} == {"Ada"}


async def test_review_pause_edit_and_continue_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    with _respond_with(target) as send:
        created = await service.create_async(request=_assert_request(pause_for_review=True))
        await _settle_async(service)
        paused = await service.get_async(created.id)
        assert paused.status == PromptGenerationStatus.AWAITING_REVIEW
        assert paused.progress.current_stage == AssertGenerationStage.SEED_GROUPS.value
        assert {_stage_of(call.kwargs["normalized_conversation"]) for call in send.call_args_list} == {
            "systematization",
            "taxonomy",
        }

        review = await service.get_review_async(created.id)
        taxonomy = review.payload["taxonomy"]
        assert isinstance(taxonomy, dict)
        categories = taxonomy["behavior_categories"]
        assert isinstance(categories, list) and isinstance(categories[0], dict)
        categories[0]["definition"] = "Explains the published 30-day return window."
        saved = await service.save_review_async(
            generation_id=created.id,
            update=PromptGenerationReviewUpdate(
                payload={"taxonomy": taxonomy, "dimensions": review.payload["dimensions"]}
            ),
        )
        assert saved.payload["taxonomy"] == taxonomy
        await service.continue_async(created.id)
        await _settle_async(service)

    assert (await service.get_async(created.id)).status == PromptGenerationStatus.COMPLETED
    objective = next(
        seed
        for seed in await sqlite_instance.get_seeds_async(dataset_name="support_policy")
        if isinstance(seed, SeedObjective)
    )
    metadata = AssertGroupMetadata.from_metadata(objective.metadata)
    assert metadata.provenance.edited_stages == (AssertGenerationStage.TAXONOMY,)
    assert metadata.taxonomy.behavior_categories[0].definition == "Explains the published 30-day return window."


async def test_review_requires_awaiting_review_async(
    service: PromptGenerationService, store: PromptGenerationStore
) -> None:
    failed = _record(status=PromptGenerationStatus.FAILED)
    await store.save_async(failed)
    with pytest.raises(PromptGenerationConflictError, match="isn't awaiting review"):
        await service.get_review_async(failed.id)


async def test_existing_dataset_name_is_rejected_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="existing", dataset_name="support_policy")], added_by="test"
    )
    preflight = await service.preflight_async(_assert_request())
    assert not preflight.valid
    assert not preflight.dataset_name_available
    with pytest.raises(PromptGenerationConflictError, match="already exists"):
        await service.create_async(request=_assert_request())


async def test_unfinished_generation_reserves_dataset_name_async(
    service: PromptGenerationService, store: PromptGenerationStore
) -> None:
    await store.save_async(_record(status=PromptGenerationStatus.FAILED))
    preflight = await service.preflight_async(_assert_request())
    assert not preflight.valid
    assert not preflight.dataset_name_available
    with pytest.raises(PromptGenerationConflictError, match="already exists"):
        await service.create_async(request=_assert_request())

    await store.save_async(_record(status=PromptGenerationStatus.CANCELLED))
    assert (await service.preflight_async(_assert_request())).dataset_name_available


async def test_preflight_reports_errors_and_estimate_async(service: PromptGenerationService) -> None:
    missing_target = await service.preflight_async(_assert_request(targets={}))
    assert not missing_target.valid
    assert any("Generation target" in error for error in missing_target.errors)
    assert missing_target.estimate is not None
    assert missing_target.estimate.max_calls >= missing_target.estimate.min_calls > 0

    invalid = await service.preflight_async(_assert_request(request={"behavior_name": "x"}))
    assert not invalid.valid
    assert invalid.estimate is None
    assert any("behavior_description" in error for error in invalid.errors)

    valid = await service.preflight_async(_assert_request())
    assert valid.valid and valid.errors == []


async def test_unknown_target_and_role_are_rejected_async(service: PromptGenerationService) -> None:
    with pytest.raises(ValueError, match="isn't registered"):
        await service.create_async(request=_assert_request(targets={"generation": "missing"}))
    with pytest.raises(ValueError, match="no target roles"):
        await service.create_async(request=_assert_request(targets={"generation": TARGET_NAME, "judge": TARGET_NAME}))


async def test_cancel_keeps_checkpoint_and_retry_resumes_async(
    service: PromptGenerationService, target: PromptTarget
) -> None:
    reached_groups = asyncio.Event()
    release = asyncio.Event()

    async def gated_async(*, normalized_conversation: list[Message]) -> list[Message]:
        if _stage_of(normalized_conversation) == "seed_groups" and not release.is_set():
            reached_groups.set()
            await release.wait()
        return await assert_generation_response_async(normalized_conversation=normalized_conversation)

    with _respond_with(target, gated_async) as send:
        created = await service.create_async(request=_assert_request())
        await wait_for_event_async(event=reached_groups, tasks=list(service._tasks.values()))
        cancelled = await service.cancel_async(created.id)
        assert cancelled.status == PromptGenerationStatus.CANCELLED
        assert cancelled.progress.current_stage == AssertGenerationStage.SEED_GROUPS.value

        release.set()
        await service.retry_async(created.id)
        await _settle_async(service)

    assert (await service.get_async(created.id)).status == PromptGenerationStatus.COMPLETED
    stages = [_stage_of(call.kwargs["normalized_conversation"]) for call in send.call_args_list]
    assert stages.count("systematization") == 1
    assert stages.count("taxonomy") == 1


async def test_failure_is_recorded_and_retry_completes_async(
    service: PromptGenerationService, target: PromptTarget
) -> None:
    failed_once = False

    async def flaky_async(*, normalized_conversation: list[Message]) -> list[Message]:
        nonlocal failed_once
        if _stage_of(normalized_conversation) == "seed_groups" and not failed_once:
            failed_once = True
            raise RuntimeError("provider unavailable")
        return await assert_generation_response_async(normalized_conversation=normalized_conversation)

    with _respond_with(target, flaky_async):
        created = await service.create_async(request=_assert_request())
        await _settle_async(service)
        failed = await service.get_async(created.id)
        assert failed.status == PromptGenerationStatus.FAILED
        assert failed.error and "provider unavailable" in failed.error

        await service.retry_async(created.id)
        await _settle_async(service)

    retried = await service.get_async(created.id)
    assert retried.status == PromptGenerationStatus.COMPLETED
    assert retried.error is None


async def test_shutdown_interrupts_and_reconcile_marks_stale_records_async(
    sqlite_instance: SQLiteMemory, store: PromptGenerationStore, target: PromptTarget, no_retry_wait: None
) -> None:
    gate = asyncio.Event()

    async def blocked_async(*, normalized_conversation: list[Message]) -> list[Message]:
        gate.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    first = PromptGenerationService(memory=sqlite_instance, store=store, target_lookup=lambda _: target)
    with _respond_with(target, blocked_async):
        created = await first.create_async(request=_assert_request())
        await wait_for_event_async(event=gate, tasks=list(first._tasks.values()))
        await first.shutdown_async()
    assert (await store.get_async(created.id)).status == PromptGenerationStatus.INTERRUPTED  # type: ignore[union-attr]

    stale = _record(status=PromptGenerationStatus.RUNNING, dataset_name="stale")
    await store.save_async(stale)
    second = PromptGenerationService(memory=sqlite_instance, store=store, target_lookup=lambda _: target)
    assert (await second.get_async(stale.id)).status == PromptGenerationStatus.INTERRUPTED
    assert await second.reconcile_interrupted_async() == 0
    with pytest.raises(PromptGenerationConflictError, match="shutting down"):
        await first.retry_async(created.id)


async def test_target_objective_generation_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    async def objectives_async(*, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1].get_piece()
        count = json.loads(request.converted_value)["count"]
        body = json.dumps({"objectives": [f"Objective {index}" for index in range(count)]})
        return [
            MessagePiece(role="assistant", original_value=body, conversation_id=request.conversation_id).to_message()
        ]

    request = PromptGenerationCreateRequest(
        generator="target_objective",
        dataset_name="objectives",
        request={"instructions": "Ask for private order details.", "count": 3, "harm_categories": ["privacy"]},
        targets={"generation": TARGET_NAME},
    )
    assert (await service.preflight_async(request)).estimate.max_calls == 1  # type: ignore[union-attr]
    with _respond_with(target, objectives_async):
        created = await service.create_async(request=request)
        await _settle_async(service)

    record = await service.get_async(created.id)
    assert record.status == PromptGenerationStatus.COMPLETED
    assert record.summary == {"objective_count": 3}
    assert record.progress == PromptGenerationProgress(
        completed_stages=["objectives"], items_completed=3, items_total=3
    )
    seeds = await sqlite_instance.get_seeds_async(dataset_name="objectives")
    assert sorted(seed.value for seed in seeds) == ["Objective 0", "Objective 1", "Objective 2"]
    assert all(seed.harm_categories == ["privacy"] for seed in seeds)
    with pytest.raises(ValueError, match="no review step"):
        service._adapters["target_objective"].get_review(checkpoint={})


async def test_batch_creates_generations_with_shared_settings_async(
    service: PromptGenerationService, target: PromptTarget
) -> None:
    config = make_assert_config().model_dump(mode="json")
    request = PromptGenerationBatchCreateRequest(
        generator="assert",
        label="Support suite",
        items=[
            {"dataset_name": "suite_returns", "request": config},
            {"dataset_name": "suite_privacy", "request": {**config, "behavior_description": "Protect private data."}},
        ],
        targets={"generation": TARGET_NAME},
    )
    with _respond_with(target):
        created = await service.create_batch_async(request=request)
        assert {record.status for record in created} == {PromptGenerationStatus.QUEUED}
        await service.cancel_async(created[1].id)
        await _settle_async(service)

    records = {record.dataset_name: record for record in await service.list_async()}
    assert records["suite_returns"].status == PromptGenerationStatus.COMPLETED
    assert records["suite_privacy"].status == PromptGenerationStatus.CANCELLED
    assert {record.group_id for record in records.values()} == {created[0].group_id}
    assert created[0].group_id is not None
    assert {record.group_label for record in records.values()} == {"Support suite"}


async def test_batch_rejects_duplicate_dataset_names_async(service: PromptGenerationService) -> None:
    config = make_assert_config().model_dump(mode="json")
    with pytest.raises(ValueError, match="different dataset name"):
        await service.create_batch_async(
            request=PromptGenerationBatchCreateRequest(
                generator="assert",
                label="Duplicate",
                items=[{"dataset_name": "same", "request": config}, {"dataset_name": "same", "request": config}],
                targets={"generation": TARGET_NAME},
            )
        )


async def test_delete_refuses_active_generations_and_keeps_the_dataset_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    with _respond_with(target):
        created = await service.create_async(request=_assert_request())
        with pytest.raises(PromptGenerationConflictError, match="Cancel the generation"):
            await service.delete_async(created.id)
        await _settle_async(service)
    await service.delete_async(created.id)
    with pytest.raises(PromptGenerationNotFoundError):
        await service.get_async(created.id)
    assert len(await sqlite_instance.get_seeds_async(dataset_name="support_policy")) > 0


async def test_request_from_dataset_reads_assert_metadata_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    with _respond_with(target):
        await service.create_async(request=_assert_request())
        await _settle_async(service)

    with patch.object(sqlite_instance, "get_seeds_async") as read_everything:
        reconstructed = await service.request_from_dataset_async("dataset:named:support_policy")
    read_everything.assert_not_called()
    assert reconstructed.generator == "assert"
    assert reconstructed.request == make_assert_config().model_dump(mode="json")

    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="plain", dataset_name="plain")], added_by="test"
    )
    with pytest.raises(PromptGenerationNotFoundError):
        await service.request_from_dataset_async("dataset:named:plain")
    with pytest.raises(ValueError, match="Choose a named dataset"):
        await service.request_from_dataset_async("dataset:unnamed")
    with pytest.raises(ValueError, match="Invalid dataset selection key"):
        await service.request_from_dataset_async("support_policy")


async def test_estimate_is_exact_after_review_pause_async(
    service: PromptGenerationService, target: PromptTarget
) -> None:
    with _respond_with(target):
        created = await service.create_async(request=_assert_request(pause_for_review=True))
        await _settle_async(service)
    estimate = await service.get_estimate_async(created.id)
    assert estimate.exact_calls
    assert [stage.stage for stage in estimate.stages] == ["seed_groups"]


async def test_store_skips_unreadable_records_async(store: PromptGenerationStore, tmp_path: Path) -> None:
    directory = tmp_path / PromptGenerationStore.DIRECTORY_NAME
    directory.mkdir()
    (directory / "broken.json").write_text("{not json", encoding="utf-8")
    assert await store.list_async() == []
    with pytest.raises(ValueError, match="Invalid prompt generation ID"):
        await store.delete_async("../escape")


async def test_shutdown_lets_a_dataset_save_finish_async(
    sqlite_instance: SQLiteMemory, store: PromptGenerationStore, target: PromptTarget, no_retry_wait: None
) -> None:
    saving = asyncio.Event()
    release = asyncio.Event()
    save_dataset = sqlite_instance.add_seed_datasets_to_memory_async

    async def held_save_async(**kwargs: Any) -> None:
        saving.set()
        await release.wait()
        await save_dataset(**kwargs)

    generation_service = PromptGenerationService(memory=sqlite_instance, store=store, target_lookup=lambda _: target)
    with (
        _respond_with(target),
        patch.object(sqlite_instance, "add_seed_datasets_to_memory_async", side_effect=held_save_async),
    ):
        created = await generation_service.create_async(request=_assert_request())
        await wait_for_event_async(event=saving, tasks=list(generation_service._tasks.values()))
        with pytest.raises(PromptGenerationConflictError, match="can't be cancelled"):
            await generation_service.cancel_async(created.id)
        shutdown = asyncio.create_task(generation_service.shutdown_async())
        await asyncio.sleep(0)
        release.set()
        await wait_for_completion_async(future=shutdown)

    record = await store.get_async(created.id)
    assert record is not None
    assert record.status == PromptGenerationStatus.COMPLETED
    assert len(await sqlite_instance.get_seeds_async(dataset_name="support_policy")) > 0


async def test_retry_finishes_a_dataset_saved_before_an_interruption_async(
    service: PromptGenerationService, target: PromptTarget, store: PromptGenerationStore, sqlite_instance: SQLiteMemory
) -> None:
    with _respond_with(target):
        created = await service.create_async(request=_assert_request())
        await _settle_async(service)
    completed = await store.get_async(created.id)
    assert completed is not None
    await store.save_async(
        completed.model_copy(
            update={"status": PromptGenerationStatus.INTERRUPTED, "summary": None, "completed_at": None}
        )
    )

    with (
        _respond_with(target) as send,
        patch.object(sqlite_instance, "add_seed_datasets_to_memory_async") as save_dataset,
    ):
        await service.retry_async(created.id)
        await _settle_async(service)
    send.assert_not_called()
    save_dataset.assert_not_called()
    retried = await service.get_async(created.id)
    assert retried.status == PromptGenerationStatus.COMPLETED
    assert retried.summary is not None


async def test_retry_rejects_a_dataset_saved_by_another_source_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    async def failing_async(*, normalized_conversation: list[Message]) -> list[Message]:
        if _stage_of(normalized_conversation) == "seed_groups":
            raise RuntimeError("provider unavailable")
        return await assert_generation_response_async(normalized_conversation=normalized_conversation)

    with _respond_with(target, failing_async):
        created = await service.create_async(request=_assert_request())
        await _settle_async(service)
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="someone else's", dataset_name="support_policy")], added_by="test"
    )
    with pytest.raises(PromptGenerationConflictError, match="already exists"):
        await service.retry_async(created.id)


async def test_batch_checks_dataset_names_against_one_snapshot_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    config = make_assert_config().model_dump(mode="json")
    request = PromptGenerationBatchCreateRequest(
        generator="assert",
        label="Suite",
        items=[{"dataset_name": f"suite_{index}", "request": config} for index in range(3)],
        targets={"generation": TARGET_NAME},
    )
    with _respond_with(target):
        with patch.object(
            sqlite_instance, "get_seed_dataset_names_async", wraps=sqlite_instance.get_seed_dataset_names_async
        ) as names:
            await service.create_batch_async(request=request)
        assert names.await_count == 1
        await _settle_async(service)


async def test_store_replaces_records_atomically_and_caches_reads_async(tmp_path: Path) -> None:
    store = _local_store(tmp_path)
    record = _record(status=PromptGenerationStatus.QUEUED, dataset_name="atomic")
    await store.save_async(record)
    with (
        patch("pyrit.backend.services.prompt_generation_store.os.replace", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        await store.save_async(record.model_copy(update={"dataset_name": "changed"}))

    reopened = _local_store(tmp_path)
    assert [item.dataset_name for item in await reopened.list_async()] == ["atomic"]
    with patch.object(DiskStorageIO, "read_file_async", side_effect=AssertionError("cached reads only")):
        assert (await reopened.get_async(record.id)) is not None
        assert len(await reopened.list_async()) == 1
