# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the prompt generation job service, with real generators, real memory, and a mock target."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.models.prompt_generations import (
    PromptGenerationBatchCreateRequest,
    PromptGenerationCreateRequest,
    PromptGenerationReviewUpdate,
)
from pyrit.backend.services.prompt_generation_service import (
    PromptGenerationConflictError,
    PromptGenerationNotFoundError,
    PromptGenerationService,
)
from pyrit.datasets import TargetObjectiveProvider
from pyrit.executor.promptgen import TargetObjectiveGeneratorResult
from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.models import (
    AssertGenerationStage,
    AssertGroupMetadata,
    Message,
    MessagePiece,
    PromptGenerationProgress,
    PromptGenerationRecord,
    PromptGenerationStatus,
    SeedObjective,
    TargetObjectiveGenerationRequest,
)
from pyrit.prompt_target import PromptTarget
from unit.assert_mocks import (
    assert_generation_response_async,
    make_assert_config,
    make_assert_generation_target,
    make_assert_taxonomy,
)
from unit.async_utils import settle_tasks_async, wait_for_completion_async, wait_for_event_async

TARGET_NAME = "generation_target"


@pytest.fixture
def no_retry_wait() -> Iterator[None]:
    with patch.dict(
        "os.environ",
        {"RETRY_MAX_NUM_ATTEMPTS": "2", "RETRY_WAIT_MIN_SECONDS": "0", "RETRY_WAIT_MAX_SECONDS": "0"},
    ):
        yield


@pytest.fixture
def target(patch_central_database: MagicMock) -> PromptTarget:
    return make_assert_generation_target()


def _new_service(memory: MemoryInterface, target: PromptTarget) -> PromptGenerationService:
    return PromptGenerationService(memory=memory, target_lookup=lambda name: target if name == TARGET_NAME else None)


@pytest.fixture
async def service(
    sqlite_instance: SQLiteMemory, target: PromptTarget, no_retry_wait: None
) -> AsyncIterator[PromptGenerationService]:
    generation_service = _new_service(sqlite_instance, target)
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
        id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        generator="assert",
        dataset_name=dataset_name,
        status=status,
        request=make_assert_config().model_dump(mode="json"),
        created_at=now,
        updated_at=now,
    )


async def _read_async(memory: MemoryInterface, record: PromptGenerationRecord) -> PromptGenerationRecord:
    stored = await memory.get_prompt_generation_async(generation_id=record.id)
    assert stored is not None
    return stored


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

    record = await service.get_async(str(created.id))
    assert record.status == PromptGenerationStatus.COMPLETED
    assert record.execution_id is None
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
        generation_id = str(created.id)
        await _settle_async(service)
        paused = await service.get_async(generation_id)
        assert paused.status == PromptGenerationStatus.AWAITING_REVIEW
        assert paused.execution_id is None
        assert paused.progress.current_stage == AssertGenerationStage.SEED_GROUPS.value
        assert {_stage_of(call.kwargs["normalized_conversation"]) for call in send.call_args_list} == {
            "systematization",
            "taxonomy",
        }

        review = await service.get_review_async(generation_id)
        assert review.generation_id == generation_id
        taxonomy = review.payload["taxonomy"]
        assert isinstance(taxonomy, dict)
        categories = taxonomy["behavior_categories"]
        assert isinstance(categories, list) and isinstance(categories[0], dict)
        categories[0]["definition"] = "Explains the published 30-day return window."
        saved = await service.save_review_async(
            generation_id=generation_id,
            update=PromptGenerationReviewUpdate(
                payload={"taxonomy": taxonomy, "dimensions": review.payload["dimensions"]}
            ),
        )
        assert saved.payload["taxonomy"] == taxonomy
        await service.continue_async(generation_id)
        await _settle_async(service)

    assert (await service.get_async(generation_id)).status == PromptGenerationStatus.COMPLETED
    objective = next(
        seed
        for seed in await sqlite_instance.get_seeds_async(dataset_name="support_policy")
        if isinstance(seed, SeedObjective)
    )
    metadata = AssertGroupMetadata.from_metadata(objective.metadata)
    assert metadata.provenance.edited_stages == (AssertGenerationStage.TAXONOMY,)
    assert metadata.taxonomy.behavior_categories[0].definition == "Explains the published 30-day return window."


async def test_review_requires_awaiting_review_async(
    service: PromptGenerationService, sqlite_instance: SQLiteMemory
) -> None:
    failed = _record(status=PromptGenerationStatus.FAILED)
    await sqlite_instance.add_prompt_generations_async(generations=[failed])
    with pytest.raises(PromptGenerationConflictError, match="isn't awaiting review"):
        await service.get_review_async(str(failed.id))


async def test_malformed_and_unknown_ids_are_not_found_async(service: PromptGenerationService) -> None:
    with pytest.raises(PromptGenerationNotFoundError):
        await service.get_async("../escape")
    with pytest.raises(PromptGenerationNotFoundError):
        await service.cancel_async(str(uuid.uuid4()))


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
    service: PromptGenerationService, sqlite_instance: SQLiteMemory
) -> None:
    failed = _record(status=PromptGenerationStatus.FAILED)
    await sqlite_instance.add_prompt_generations_async(generations=[failed])
    preflight = await service.preflight_async(_assert_request())
    assert not preflight.valid
    assert not preflight.dataset_name_available
    with pytest.raises(PromptGenerationConflictError, match="already exists"):
        await service.create_async(request=_assert_request())

    cancelled = failed.model_copy(update={"status": PromptGenerationStatus.CANCELLED})
    assert await sqlite_instance.try_update_prompt_generation_async(
        generation=cancelled, expected_statuses=[PromptGenerationStatus.FAILED], fields=["status"]
    )
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
        generation_id = str(created.id)
        await wait_for_event_async(event=reached_groups, tasks=list(service._tasks.values()))
        assert service.has_active_work()
        cancelled = await service.cancel_async(generation_id)
        assert cancelled.status == PromptGenerationStatus.CANCELLED
        assert cancelled.execution_id is None
        assert cancelled.progress.current_stage == AssertGenerationStage.SEED_GROUPS.value

        release.set()
        await service.retry_async(generation_id)
        await _settle_async(service)

    assert (await service.get_async(generation_id)).status == PromptGenerationStatus.COMPLETED
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
        generation_id = str(created.id)
        await _settle_async(service)
        failed = await service.get_async(generation_id)
        assert failed.status == PromptGenerationStatus.FAILED
        assert failed.execution_id is None
        assert failed.error and "provider unavailable" in failed.error

        await service.retry_async(generation_id)
        await _settle_async(service)

    retried = await service.get_async(generation_id)
    assert retried.status == PromptGenerationStatus.COMPLETED
    assert retried.error is None


async def test_shutdown_interrupts_a_running_generation_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    gate = asyncio.Event()

    async def blocked_async(*, normalized_conversation: list[Message]) -> list[Message]:
        gate.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    with _respond_with(target, blocked_async):
        created = await service.create_async(request=_assert_request())
        await wait_for_event_async(event=gate, tasks=list(service._tasks.values()))
        await service.shutdown_async()
    interrupted = await _read_async(sqlite_instance, created)
    assert interrupted.status == PromptGenerationStatus.INTERRUPTED
    assert interrupted.execution_id is None


async def test_shutdown_rejects_resumed_work_async(
    service: PromptGenerationService, sqlite_instance: SQLiteMemory
) -> None:
    failed = _record(status=PromptGenerationStatus.FAILED)
    await sqlite_instance.add_prompt_generations_async(generations=[failed])
    await service.shutdown_async()
    with pytest.raises(PromptGenerationConflictError, match="shutting down"):
        await service.retry_async(str(failed.id))
    assert (await _read_async(sqlite_instance, failed)).status == PromptGenerationStatus.FAILED


async def test_first_use_marks_stale_sqlite_generations_interrupted_async(
    service: PromptGenerationService, sqlite_instance: SQLiteMemory
) -> None:
    stale = _record(status=PromptGenerationStatus.RUNNING, dataset_name="stale")
    await sqlite_instance.add_prompt_generations_async(generations=[stale])
    assert (await service.get_async(str(stale.id))).status == PromptGenerationStatus.INTERRUPTED
    assert await service.reconcile_interrupted_async() == 0


async def test_reconcile_leaves_shared_memory_alone_async() -> None:
    running = _record(status=PromptGenerationStatus.RUNNING)
    memory = MagicMock(spec=MemoryInterface)
    memory.get_prompt_generations_async = AsyncMock(return_value=[running])
    memory.try_update_prompt_generation_async = AsyncMock(return_value=True)
    shared = PromptGenerationService(memory=memory, adapters={}, target_lookup=lambda _: None)

    assert await shared.list_async() == [running]
    assert await shared.reconcile_interrupted_async() == 0
    memory.try_update_prompt_generation_async.assert_not_awaited()
    memory.get_prompt_generations_async.assert_awaited_once_with()


async def test_another_service_on_the_same_memory_sees_new_generations_async(
    sqlite_instance: SQLiteMemory, target: PromptTarget, no_retry_wait: None
) -> None:
    first = _new_service(sqlite_instance, target)
    second = _new_service(sqlite_instance, target)
    try:
        assert await second.list_async() == []
        with _respond_with(target):
            created = await first.create_async(request=_assert_request(pause_for_review=True))
            await _settle_async(first)
        listed = await second.list_async()
        assert [record.id for record in listed] == [created.id]
        assert listed[0].status == PromptGenerationStatus.AWAITING_REVIEW
        assert (await second.get_review_async(str(created.id))).stage == AssertGenerationStage.DIMENSIONS.value
    finally:
        await first.shutdown_async()
        await second.shutdown_async()


async def test_cancel_from_another_service_stops_the_owner_without_overwriting_async(
    sqlite_instance: SQLiteMemory, target: PromptTarget, no_retry_wait: None
) -> None:
    reached_groups = asyncio.Event()
    release = asyncio.Event()

    async def gated_async(*, normalized_conversation: list[Message]) -> list[Message]:
        if _stage_of(normalized_conversation) == "seed_groups":
            reached_groups.set()
            await release.wait()
        return await assert_generation_response_async(normalized_conversation=normalized_conversation)

    owner = _new_service(sqlite_instance, target)
    other = _new_service(sqlite_instance, target)
    try:
        await other.list_async()
        with _respond_with(target, gated_async) as send:
            created = await owner.create_async(request=_assert_request())
            await wait_for_event_async(event=reached_groups, tasks=list(owner._tasks.values()))
            running = await _read_async(sqlite_instance, created)
            assert running.status == PromptGenerationStatus.RUNNING
            assert running.execution_id is not None

            cancelled = await other.cancel_async(str(created.id))
            assert cancelled.status == PromptGenerationStatus.CANCELLED
            calls_at_cancel = send.call_count
            release.set()
            await _settle_async(owner)

        assert await _read_async(sqlite_instance, created) == cancelled
        assert send.call_count == calls_at_cancel
        assert await sqlite_instance.get_seeds_async(dataset_name="support_policy") == []
        assert not owner.has_active_work()
    finally:
        release.set()
        await owner.shutdown_async()
        await other.shutdown_async()


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

    record = await service.get_async(str(created.id))
    assert record.status == PromptGenerationStatus.COMPLETED
    assert record.summary is None
    assert record.progress == PromptGenerationProgress(
        completed_stages=["objectives"], items_completed=3, items_total=3
    )
    seeds = await sqlite_instance.get_seeds_async(dataset_name="objectives")
    assert sorted(seed.value for seed in seeds) == ["Objective 0", "Objective 1", "Objective 2"]
    assert all(seed.harm_categories == ["privacy"] for seed in seeds)
    reconstructed = await service.request_from_dataset_async("dataset:named:objectives")
    assert reconstructed.generator == "target_objective"
    assert reconstructed.request == {
        "instructions": "Ask for private order details.",
        "count": 3,
        "harm_categories": ["privacy"],
    }


async def test_create_rejects_a_blank_harm_category_async(service: PromptGenerationService) -> None:
    request = PromptGenerationCreateRequest(
        generator="target_objective",
        dataset_name="objectives",
        request={"instructions": "Ask for private order details.", "count": 2, "harm_categories": [" "]},
        targets={"generation": TARGET_NAME},
    )
    assert not (await service.preflight_async(request)).valid
    with pytest.raises(ValueError, match="harm_categories"):
        await service.create_async(request=request)
    assert await service.list_async() == []


async def test_create_limits_the_objective_count_async(service: PromptGenerationService) -> None:
    request = PromptGenerationCreateRequest(
        generator="target_objective",
        dataset_name="objectives",
        request={"instructions": "Ask for private order details.", "count": 1_001},
        targets={"generation": TARGET_NAME},
    )
    with pytest.raises(ValueError, match="count"):
        await service.create_async(request=request)


async def test_request_from_dataset_reads_a_request_beyond_the_create_limits_async(
    service: PromptGenerationService, sqlite_instance: SQLiteMemory
) -> None:
    # The SDK has no count limit, so Edit & regenerate shows the stored request and create applies the limit.
    request = TargetObjectiveGenerationRequest(instructions="Ask for private data.", count=1_001)
    result = TargetObjectiveGeneratorResult(objectives=["Objective"], conversation_id=str(uuid.uuid4()))
    dataset = TargetObjectiveProvider.package_result(dataset_name="sdk_objectives", result=result, request=request)
    await sqlite_instance.add_seeds_to_memory_async(seeds=dataset.seeds, added_by="sdk")

    reconstructed = await service.request_from_dataset_async("dataset:named:sdk_objectives")

    assert reconstructed.generator == "target_objective"
    assert reconstructed.request == request.model_dump(mode="json")


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
        await service.cancel_async(str(created[1].id))
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


async def test_batch_with_a_taken_name_creates_nothing_async(
    service: PromptGenerationService, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="existing", dataset_name="suite_1")], added_by="test"
    )
    config = make_assert_config().model_dump(mode="json")
    request = PromptGenerationBatchCreateRequest(
        generator="assert",
        label="Suite",
        items=[{"dataset_name": f"suite_{index}", "request": config} for index in range(3)],
        targets={"generation": TARGET_NAME},
    )
    with pytest.raises(PromptGenerationConflictError, match="named 'suite_1' already exists"):
        await service.create_batch_async(request=request)
    assert await service.list_async() == []
    assert not service.has_active_work()


async def test_delete_refuses_active_generations_and_keeps_the_dataset_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    with _respond_with(target):
        created = await service.create_async(request=_assert_request())
        generation_id = str(created.id)
        with pytest.raises(PromptGenerationConflictError, match="Cancel the generation"):
            await service.delete_async(generation_id)
        await _settle_async(service)
    await service.delete_async(generation_id)
    with pytest.raises(PromptGenerationNotFoundError):
        await service.get_async(generation_id)
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


@pytest.mark.parametrize(
    ("selection_key", "error", "match"),
    [
        ("dataset:named:plain", PromptGenerationNotFoundError, "doesn't record a generation request"),
        ("dataset:named:invalid_request", PromptGenerationNotFoundError, "doesn't record a generation request"),
        ("dataset:named:invalid_assert", PromptGenerationNotFoundError, "doesn't record a generation request"),
        ("dataset:unnamed", ValueError, "Choose a named dataset"),
        ("support_policy", ValueError, "Invalid dataset selection key"),
    ],
)
async def test_request_from_dataset_rejects_datasets_without_a_valid_request_async(
    service: PromptGenerationService,
    sqlite_instance: SQLiteMemory,
    selection_key: str,
    error: type[Exception],
    match: str,
) -> None:
    invalid = {TargetObjectiveGenerationRequest.METADATA_KEY: {"instructions": " ", "count": 1}}
    invalid_assert = {AssertGroupMetadata.METADATA_KEY: {"unexpected": True}}
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedObjective(value="plain", dataset_name="plain"),
            SeedObjective(value="invalid", dataset_name="invalid_request", metadata=invalid),
            SeedObjective(value="invalid assert", dataset_name="invalid_assert", metadata=invalid_assert),
        ],
        added_by="test",
    )
    with pytest.raises(error, match=match):
        await service.request_from_dataset_async(selection_key)


async def test_request_from_dataset_skips_invalid_assert_metadata_for_valid_adapter_async(
    service: PromptGenerationService,
    sqlite_instance: SQLiteMemory,
) -> None:
    target_request = TargetObjectiveGenerationRequest(instructions="Ask nicely", count=2)
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedObjective(
                value="mixed",
                dataset_name="mixed_metadata",
                metadata={
                    AssertGroupMetadata.METADATA_KEY: {"unexpected": True},
                    **target_request.to_metadata(),
                },
            ),
        ],
        added_by="test",
    )
    resolved = await service.request_from_dataset_async("dataset:named:mixed_metadata")
    assert resolved.generator == "target_objective"
    assert resolved.request == target_request.model_dump(mode="json")


async def test_estimate_is_exact_after_review_pause_async(
    service: PromptGenerationService, target: PromptTarget
) -> None:
    with _respond_with(target):
        created = await service.create_async(request=_assert_request(pause_for_review=True))
        await _settle_async(service)
    estimate = await service.get_estimate_async(str(created.id))
    assert estimate.exact_calls
    assert [stage.stage for stage in estimate.stages] == ["seed_groups"]


async def test_shutdown_lets_a_dataset_save_finish_async(
    sqlite_instance: SQLiteMemory, target: PromptTarget, no_retry_wait: None
) -> None:
    saving = asyncio.Event()
    release = asyncio.Event()
    commit_dataset = sqlite_instance.commit_prompt_generation_dataset_async

    async def held_commit_async(**kwargs: Any) -> bool:
        saving.set()
        await release.wait()
        return await commit_dataset(**kwargs)

    generation_service = _new_service(sqlite_instance, target)
    with (
        _respond_with(target),
        patch.object(sqlite_instance, "commit_prompt_generation_dataset_async", side_effect=held_commit_async),
    ):
        created = await generation_service.create_async(request=_assert_request())
        await wait_for_event_async(event=saving, tasks=list(generation_service._tasks.values()))
        with pytest.raises(PromptGenerationConflictError, match="saving its dataset, so it can't be cancelled"):
            await generation_service.cancel_async(str(created.id))
        shutdown = asyncio.create_task(generation_service.shutdown_async())
        await asyncio.sleep(0)
        release.set()
        await wait_for_completion_async(future=shutdown)

    record = await _read_async(sqlite_instance, created)
    assert record.status == PromptGenerationStatus.COMPLETED
    assert len(await sqlite_instance.get_seeds_async(dataset_name="support_policy")) > 0


async def test_an_orphaned_running_generation_can_be_cancelled_then_retried_or_deleted_async(
    sqlite_instance: SQLiteMemory, service: PromptGenerationService, target: PromptTarget
) -> None:
    # Shared memory skips reconcile, so a backend that stopped while running or saving leaves the record running.
    await service.list_async()
    orphans = [
        _record(status=PromptGenerationStatus.RUNNING, dataset_name=name).model_copy(
            update={"id": uuid.uuid4(), "execution_id": uuid.uuid4(), "targets": {"generation": TARGET_NAME}}
        )
        for name in ("retried", "deleted")
    ]
    await sqlite_instance.add_prompt_generations_async(generations=orphans)
    assert not await sqlite_instance.is_dataset_name_available_async(dataset_name="retried")
    assert not await sqlite_instance.is_dataset_name_available_async(dataset_name="deleted")

    for orphan in orphans:
        cancelled = await service.cancel_async(str(orphan.id))
        assert cancelled.status == PromptGenerationStatus.CANCELLED
        assert cancelled.execution_id is None
    assert await sqlite_instance.is_dataset_name_available_async(dataset_name="retried")
    assert await sqlite_instance.is_dataset_name_available_async(dataset_name="deleted")

    await service.delete_async(str(orphans[1].id))
    with pytest.raises(PromptGenerationNotFoundError):
        await service.get_async(str(orphans[1].id))
    with _respond_with(target):
        await service.retry_async(str(orphans[0].id))
        await _settle_async(service)
    assert (await _read_async(sqlite_instance, orphans[0])).status == PromptGenerationStatus.COMPLETED
    assert len(await sqlite_instance.get_seeds_async(dataset_name="retried")) > 0


async def test_cancel_during_another_services_save_makes_its_commit_a_no_op_async(
    sqlite_instance: SQLiteMemory, target: PromptTarget, no_retry_wait: None
) -> None:
    saving = asyncio.Event()
    release = asyncio.Event()
    commit_dataset = sqlite_instance.commit_prompt_generation_dataset_async
    commits: list[bool] = []

    async def held_commit_async(**kwargs: Any) -> bool:
        saving.set()
        await release.wait()
        commits.append(await commit_dataset(**kwargs))
        return commits[-1]

    owner = _new_service(sqlite_instance, target)
    other = _new_service(sqlite_instance, target)
    try:
        await other.list_async()
        with (
            _respond_with(target),
            patch.object(sqlite_instance, "commit_prompt_generation_dataset_async", side_effect=held_commit_async),
        ):
            created = await owner.create_async(request=_assert_request())
            await wait_for_event_async(event=saving, tasks=list(owner._tasks.values()))
            during_save = await _read_async(sqlite_instance, created)
            assert during_save.status == PromptGenerationStatus.RUNNING
            assert during_save.execution_id is not None
            with pytest.raises(PromptGenerationConflictError, match="saving its dataset, so it can't be cancelled"):
                await owner.cancel_async(str(created.id))
            cancelled = await other.cancel_async(str(created.id))
            assert cancelled.status == PromptGenerationStatus.CANCELLED
            release.set()
            await _settle_async(owner)

        assert commits == [False]
        assert await _read_async(sqlite_instance, created) == cancelled
        assert await sqlite_instance.get_seeds_async(dataset_name="support_policy") == []
    finally:
        release.set()
        await owner.shutdown_async()
        await other.shutdown_async()


async def test_retry_after_a_failed_save_saves_the_dataset_once_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    with (
        _respond_with(target),
        patch.object(
            sqlite_instance, "commit_prompt_generation_dataset_async", side_effect=RuntimeError("database unavailable")
        ),
    ):
        created = await service.create_async(request=_assert_request())
        await _settle_async(service)
    failed = await _read_async(sqlite_instance, created)
    assert failed.status == PromptGenerationStatus.FAILED
    assert failed.error and "database unavailable" in failed.error
    assert await sqlite_instance.get_seeds_async(dataset_name="support_policy") == []

    with _respond_with(target) as send:
        await service.retry_async(str(created.id))
        await _settle_async(service)
    send.assert_not_called()
    retried = await _read_async(sqlite_instance, created)
    assert retried.status == PromptGenerationStatus.COMPLETED
    assert retried.summary is not None
    objectives = [
        seed
        for seed in await sqlite_instance.get_seeds_async(dataset_name="support_policy")
        if isinstance(seed, SeedObjective)
    ]
    assert len(objectives) == 6


async def test_a_dataset_created_while_generating_fails_the_save_without_mixing_seeds_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    reached_groups = asyncio.Event()
    release = asyncio.Event()

    async def gated_async(*, normalized_conversation: list[Message]) -> list[Message]:
        if _stage_of(normalized_conversation) == "seed_groups" and not release.is_set():
            reached_groups.set()
            await release.wait()
        return await assert_generation_response_async(normalized_conversation=normalized_conversation)

    with _respond_with(target, gated_async):
        created = await service.create_async(request=_assert_request())
        await wait_for_event_async(event=reached_groups, tasks=list(service._tasks.values()))
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value="someone else's", dataset_name="support_policy")], added_by="test"
        )
        release.set()
        await _settle_async(service)

    failed = await service.get_async(str(created.id))
    assert failed.status == PromptGenerationStatus.FAILED
    assert failed.error is not None and "was created while generating" in failed.error
    # The message must not suggest the checkpoint's work carries over to a new name.
    assert "starts over and repeats the model calls" in failed.error
    seeds = await sqlite_instance.get_seeds_async(dataset_name="support_policy")
    assert [seed.value for seed in seeds] == ["someone else's"]


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
    with pytest.raises(PromptGenerationConflictError, match="already exists") as conflict:
        await service.retry_async(str(created.id))
    assert "can't resume under that name" in str(conflict.value)
    assert (await _read_async(sqlite_instance, created)).status == PromptGenerationStatus.FAILED
    assert not service.has_active_work()


async def test_pause_for_review_needs_a_reviewable_stage_async(service: PromptGenerationService) -> None:
    request = PromptGenerationCreateRequest(
        generator="target_objective",
        dataset_name="objectives",
        request={"instructions": "Ask for private order details.", "count": 3},
        targets={"generation": TARGET_NAME},
        pause_for_review=True,
    )
    preflight = await service.preflight_async(request)
    assert not preflight.valid
    assert any("no review step" in error for error in preflight.errors)
    with pytest.raises(ValueError, match="no review step"):
        await service.create_async(request=request)
    assert await service.list_async() == []


async def test_batch_members_keep_request_order_after_a_restart_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    config = make_assert_config().model_dump(mode="json")
    names = ["suite_zeta", "suite_alpha", "suite_mid"]
    with _respond_with(target):
        created = await service.create_batch_async(
            request=PromptGenerationBatchCreateRequest(
                generator="assert",
                label="Suite",
                items=[{"dataset_name": name, "request": config} for name in names],
                targets={"generation": TARGET_NAME},
            )
        )
        await _settle_async(service)
    assert [record.dataset_name for record in created] == names
    # The order comes from each member's batch position, not from creation times that SQL Server rounds together.
    assert len({record.created_at for record in created}) == 1
    assert all(record.updated_at >= record.created_at for record in created)
    restarted = _new_service(sqlite_instance, target)
    # A GUI that groups the newest-first list by group_id shows the suite as it was composed.
    assert [record.dataset_name for record in await restarted.list_async()] == names
    await restarted.shutdown_async()


async def test_a_review_saved_while_continue_queues_is_kept_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    edited = "Explains the published 30-day return window."
    read_generation = sqlite_instance.get_prompt_generation_async
    continue_read = asyncio.Event()
    save_done = asyncio.Event()

    async def read_then_wait_for_the_save_async(**kwargs: Any) -> PromptGenerationRecord | None:
        record = await read_generation(**kwargs)
        continue_read.set()
        await save_done.wait()
        return record

    with _respond_with(target):
        created = await service.create_async(request=_assert_request(pause_for_review=True))
        generation_id = str(created.id)
        await _settle_async(service)
        review = await service.get_review_async(generation_id)
        taxonomy = review.payload["taxonomy"]
        assert isinstance(taxonomy, dict)
        categories = taxonomy["behavior_categories"]
        assert isinstance(categories, list) and isinstance(categories[0], dict)
        categories[0]["definition"] = edited
        update = PromptGenerationReviewUpdate(
            payload={"taxonomy": taxonomy, "dimensions": review.payload["dimensions"]}
        )

        with patch.object(
            sqlite_instance, "get_prompt_generation_async", side_effect=read_then_wait_for_the_save_async
        ):
            queued = asyncio.create_task(service.continue_async(generation_id))
            await wait_for_event_async(event=continue_read, tasks=[queued])
        saved = await service.save_review_async(generation_id=generation_id, update=update)
        save_done.set()
        await wait_for_completion_async(future=queued)
        await _settle_async(service)

    assert saved.payload["taxonomy"] == taxonomy
    assert (await service.get_async(generation_id)).status == PromptGenerationStatus.COMPLETED
    objective = next(
        seed
        for seed in await sqlite_instance.get_seeds_async(dataset_name="support_policy")
        if isinstance(seed, SeedObjective)
    )
    metadata = AssertGroupMetadata.from_metadata(objective.metadata)
    assert metadata.taxonomy.behavior_categories[0].definition == edited
    assert metadata.provenance.edited_stages == (AssertGenerationStage.TAXONOMY,)


async def test_continue_needs_no_target_for_finished_research_async(
    sqlite_instance: SQLiteMemory, target: PromptTarget, no_retry_wait: None
) -> None:
    registry = {TARGET_NAME: target, "research_target": target}
    service = PromptGenerationService(memory=sqlite_instance, target_lookup=registry.get)
    targets = {"generation": TARGET_NAME, "research": "research_target"}
    try:
        with _respond_with(target):
            created = await service.create_async(request=_assert_request(pause_for_review=True, targets=targets))
            await _settle_async(service)
            del registry["research_target"]
            await service.continue_async(str(created.id))
            await _settle_async(service)
        assert (await service.get_async(str(created.id))).status == PromptGenerationStatus.COMPLETED
    finally:
        await service.shutdown_async()


async def test_progress_counts_stages_the_request_supplies_as_done_async(
    service: PromptGenerationService, target: PromptTarget
) -> None:
    config = make_assert_config(taxonomy=make_assert_taxonomy())
    with _respond_with(target):
        created = await service.create_async(request=_assert_request(request=config.model_dump(mode="json")))
        await _settle_async(service)
    assert created.progress.completed_stages == ["systematization", "taxonomy", "dimensions"]
    assert created.progress.current_stage == AssertGenerationStage.SEED_GROUPS.value


async def test_retry_conflicts_with_a_generation_that_took_the_released_name_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    cancelled = _record(status=PromptGenerationStatus.CANCELLED).model_copy(
        update={"targets": {"generation": TARGET_NAME}}
    )
    await sqlite_instance.add_prompt_generations_async(generations=[cancelled])
    with _respond_with(target):
        await service.create_async(request=_assert_request(pause_for_review=True))
        await _settle_async(service)

    with pytest.raises(PromptGenerationConflictError, match="already exists") as conflict:
        await service.retry_async(str(cancelled.id))
    # A cancel released the name, so the checkpoint can't be saved; a new generation starts over.
    assert "can't resume under that name" in str(conflict.value)
    assert "starts over and repeats the model calls" in str(conflict.value)
    assert (await _read_async(sqlite_instance, cancelled)).status == PromptGenerationStatus.CANCELLED


async def test_cancel_records_the_cancel_when_the_runs_own_write_fails_async(
    service: PromptGenerationService, target: PromptTarget, sqlite_instance: SQLiteMemory
) -> None:
    reached_groups = asyncio.Event()
    update_generation = sqlite_instance.try_update_prompt_generation_async
    failed_writes: list[uuid.UUID] = []

    async def gated_async(*, normalized_conversation: list[Message]) -> list[Message]:
        if _stage_of(normalized_conversation) == "seed_groups":
            reached_groups.set()
            await asyncio.Event().wait()
        return await assert_generation_response_async(normalized_conversation=normalized_conversation)

    async def first_cancel_write_fails_async(**kwargs: Any) -> bool:
        if kwargs["generation"].status == PromptGenerationStatus.CANCELLED and not failed_writes:
            failed_writes.append(kwargs["generation"].id)
            raise RuntimeError("memory unavailable")
        return await update_generation(**kwargs)

    with _respond_with(target, gated_async):
        created = await service.create_async(request=_assert_request())
        await wait_for_event_async(event=reached_groups, tasks=list(service._tasks.values()))
        with patch.object(
            sqlite_instance, "try_update_prompt_generation_async", side_effect=first_cancel_write_fails_async
        ):
            cancelled = await service.cancel_async(str(created.id))

    assert failed_writes == [created.id]
    assert cancelled.status == PromptGenerationStatus.CANCELLED
    assert (await _read_async(sqlite_instance, created)).status == PromptGenerationStatus.CANCELLED
