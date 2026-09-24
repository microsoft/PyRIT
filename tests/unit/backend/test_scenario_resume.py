# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Offline resume coverage using real scenario persistence and harmless mocked targets."""

import asyncio
from collections.abc import AsyncIterator
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.backend.services.scenario_run_service import (
    _LAUNCH_REQUEST_FIELDS,
    _LAUNCH_REQUEST_METADATA_KEY,
    ScenarioRunConflictError,
    ScenarioRunNotFoundError,
    ScenarioRunService,
    _PreparedRun,
)
from pyrit.exceptions import ScenarioPartialFailureException
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    Message,
    Parameter,
    ScenarioResult,
    ScenarioRunState,
    SeedObjective,
)
from pyrit.models.catalog.scenario import RunScenarioRequest
from pyrit.registry import ScenarioRegistry, TargetRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.core import AtomicAttack, AttackTechnique, BaselineAttackPolicy, Scenario, ScenarioTechnique
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.score import SubStringScorer
from unit.mocks import MockPromptTarget

_SCENARIO_NAME = "offline.resume"
_TARGET_NAME = "offline-target"
_DATASET_NAME = "offline-resume-objectives"
_FIRST_OBJECTIVE = "Say hello"
_SECOND_OBJECTIVE = "Say goodbye"
_LABELS = {"operator": "offline-tester", "operation": "resume-fixture"}


class _OfflineTechnique(ScenarioTechnique):
    ALL = ("all", {"all"})
    DIRECT = ("direct", {"direct"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _OfflineResumeScenario(Scenario):
    VERSION: ClassVar[int] = 1
    DEFAULT_MARKER: ClassVar[str] = "original"
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    def __init__(self, *, scenario_result_id: str | None = None) -> None:
        super().__init__(
            version=self.VERSION,
            technique_class=_OfflineTechnique,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=[_DATASET_NAME], auto_fetch=False),
            objective_scorer=SubStringScorer(substring="default"),
            scenario_result_id=scenario_result_id,
        )

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        return [
            Parameter(name="marker", description="A saved custom parameter", param_type=str, default=cls.DEFAULT_MARKER)
        ]

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        return [
            AtomicAttack(
                atomic_attack_name="direct",
                attack_technique=AttackTechnique(
                    attack=PromptSendingAttack(
                        objective_target=context.objective_target,
                        attack_scoring_config=AttackScoringConfig(objective_scorer=self._objective_scorer),
                    )
                ),
                seed_groups=list(context.seed_groups),
                memory_labels=context.memory_labels,
            )
        ]


@pytest.fixture
async def resume_environment(
    patch_central_database: object,
) -> AsyncIterator[tuple[ScenarioRunService, MockPromptTarget]]:
    memory = CentralMemory.get_memory_instance()
    await memory.add_seeds_to_memory_async(
        seeds=[
            SeedObjective(value=_FIRST_OBJECTIVE, dataset_name=_DATASET_NAME),
            SeedObjective(value=_SECOND_OBJECTIVE, dataset_name=_DATASET_NAME),
        ],
        added_by="offline-test",
    )
    with patch.object(ScenarioRegistry, "_discover"), patch.object(TargetRegistry, "_discover"):
        scenarios = ScenarioRegistry()
        scenarios.register_class(_OfflineResumeScenario, name=_SCENARIO_NAME)
        targets = TargetRegistry()
        target = MockPromptTarget()
        targets.instances.register(target, name=_TARGET_NAME)
        with (
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=scenarios),
            patch.object(TargetRegistry, "get_registry_singleton", return_value=targets),
        ):
            service = ScenarioRunService()
            try:
                yield service, target
            finally:
                await service.shutdown_async()


async def _wait_for_idle_async(service: ScenarioRunService) -> None:
    async def wait_async() -> None:
        while service.get_queue_snapshot().active is not None:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_async(), timeout=10)


async def _create_failed_run_async(*, target: MockPromptTarget, legacy: bool) -> ScenarioResult:
    registry = ScenarioRegistry.get_registry_singleton()
    scenario = await registry.create_and_initialize_async(
        _SCENARIO_NAME,
        objective_target=target,
        max_concurrency=1,
        max_retries=0,
        memory_labels=_LABELS,
        dataset_config=DatasetAttackConfiguration(dataset_names=[_DATASET_NAME], max_dataset_size=2, auto_fetch=False),
        initial_metadata={}
        if legacy
        else {
            _LAUNCH_REQUEST_METADATA_KEY: RunScenarioRequest(
                scenario_name=_SCENARIO_NAME,
                target_name=_TARGET_NAME,
                max_concurrency=1,
                include_baseline=False,
            ).model_dump(
                exclude={"initializers", "initializer_args", "scenario_params", "scenario_result_id", "labels"}
            )
        },
    )
    original_send = target._send_prompt_to_target_async

    async def fail_second_async(*, normalized_conversation: list[Message]) -> list[Message]:
        if normalized_conversation[-1].get_value() == _SECOND_OBJECTIVE:
            raise RuntimeError("Synthetic target failure")
        responses: list[Message] = await original_send(normalized_conversation=normalized_conversation)
        return responses

    with patch.object(target, "_send_prompt_to_target_async", side_effect=fail_second_async):
        with pytest.raises(ScenarioPartialFailureException):
            await scenario.run_async()
    assert scenario._scenario_result_id is not None
    result = (
        await CentralMemory.get_memory_instance().get_scenario_results_async(
            scenario_result_ids=[scenario._scenario_result_id]
        )
    )[0]
    assert result.scenario_run_state == ScenarioRunState.FAILED
    return result


async def test_resume_preserves_completed_objectives_and_original_id_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    run_id = str(stored.id)
    before = await CentralMemory.get_memory_instance().get_attack_results_async(scenario_result_id=run_id)
    completed_ids = {result.attack_result_id for result in before if result.outcome != AttackOutcome.ERROR}
    assert len(completed_ids) == 1
    failed_result = next(result for result in before if result.outcome == AttackOutcome.ERROR)
    assert _LAUNCH_REQUEST_METADATA_KEY in stored.metadata
    original_plan = stored.metadata[SCENARIO_RUN_PLAN_METADATA_KEY]
    target.prompt_sent.clear()
    # New objectives and changed current parameter defaults must not alter the saved run.
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[SeedObjective(value="A newly added objective", dataset_name=_DATASET_NAME)],
        added_by="offline-test",
    )
    with (
        patch.object(_OfflineResumeScenario, "DEFAULT_MARKER", "changed-default"),
        patch.object(service, "_start_run_locked_async", wraps=service._start_run_locked_async) as start,
    ):
        response = await service.resume_run_async(scenario_result_id=run_id)
        await _wait_for_idle_async(service)
    request = start.await_args.kwargs["request"]
    assert request.max_concurrency == 1
    assert request.max_retries == 0
    assert request.include_baseline is False
    assert request.scenario_params == {"marker": "original"}
    assert request.labels == _LABELS
    assert response.scenario_result_id == run_id
    assert target.prompt_sent == [_SECOND_OBJECTIVE]
    after = (await CentralMemory.get_memory_instance().get_scenario_results_async(scenario_result_ids=[run_id]))[0]
    results = await CentralMemory.get_memory_instance().get_attack_results_async(scenario_result_id=run_id)
    assert after.scenario_run_state == ScenarioRunState.COMPLETED
    assert after.labels == _LABELS
    assert after.scenario_identifier.params["marker"] == "original"
    assert after.metadata[SCENARIO_RUN_PLAN_METADATA_KEY] == original_plan
    assert {result.attack_result_id for result in results}.issuperset(result.attack_result_id for result in before)
    assert all(result.attribution_parent_id == run_id for result in results)
    assert all(
        result.operator == _LABELS["operator"] and result.operation == _LABELS["operation"] for result in results
    )
    assert sum(result.objective == _FIRST_OBJECTIVE for result in results) == 1
    assert len(await CentralMemory.get_memory_instance().get_scenario_results_async()) == 1
    detail = await service.get_run_async(scenario_result_id=run_id)
    history = await service.list_runs_async()
    assert detail is not None
    assert detail.status == ScenarioRunState.COMPLETED
    assert detail.error is None
    assert detail.error_type is None
    assert len(detail.failed_attacks) == 1
    assert detail.failed_attacks[0].objective == _SECOND_OBJECTIVE
    assert detail.failed_attacks[0].error_message == failed_result.error_message
    assert detail.failed_attacks[0].error_type == failed_result.error_type
    assert len(history.items) == 1
    summary = history.items[0]
    assert summary.scenario_result_id == run_id
    assert summary.status == ScenarioRunState.COMPLETED
    assert summary.error is None
    assert summary.error_type is None
    assert summary.error_attacks == detail.error_attacks == 1


async def test_resume_without_launch_metadata_is_rejected_without_initialization_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=True)
    run_id = str(stored.id)
    target.prompt_sent.clear()
    with patch.object(service, "_prepare_run_blocking") as prepare:
        with pytest.raises(ScenarioRunConflictError, match="older run.*cannot be resumed through the GUI"):
            await service.resume_run_async(scenario_result_id=run_id)
        prepare.assert_not_called()
    after = await CentralMemory.get_memory_instance().get_scenario_results_async(scenario_result_ids=[run_id])
    assert after[0].model_dump() == stored.model_dump()
    assert service.get_queue_snapshot().active is None
    assert service.get_queue_snapshot().queued == []
    assert target.prompt_sent == []


@pytest.mark.parametrize("failure", ["initialization", "execution"])
async def test_resume_failure_preserves_saved_results_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget], failure: str
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    run_id = str(stored.id)
    before = await CentralMemory.get_memory_instance().get_attack_results_async(scenario_result_id=run_id)
    target.prompt_sent.clear()
    if failure == "initialization":
        with patch.object(_OfflineResumeScenario, "VERSION", 2):
            with pytest.raises(ValueError, match="does not match the current"):
                await service.resume_run_async(scenario_result_id=run_id)
    else:
        with patch.object(target, "_send_prompt_to_target_async", side_effect=RuntimeError("Still unavailable")):
            await service.resume_run_async(scenario_result_id=run_id)
            await _wait_for_idle_async(service)
    after = (await CentralMemory.get_memory_instance().get_scenario_results_async(scenario_result_ids=[run_id]))[0]
    results = await CentralMemory.get_memory_instance().get_attack_results_async(scenario_result_id=run_id)
    assert after.scenario_run_state == ScenarioRunState.FAILED
    assert {result.attack_result_id for result in results}.issuperset(result.attack_result_id for result in before)
    assert target.prompt_sent == []
    assert service.get_queue_snapshot().active is None
    assert service.get_queue_snapshot().queued == []

    response = await service.resume_run_async(scenario_result_id=run_id)
    await _wait_for_idle_async(service)
    assert response.scenario_result_id == run_id
    assert target.prompt_sent == [_SECOND_OBJECTIVE]


@pytest.mark.parametrize(
    "state",
    [
        ScenarioRunState.CREATED,
        ScenarioRunState.QUEUED,
        ScenarioRunState.IN_PROGRESS,
        ScenarioRunState.COMPLETED,
        ScenarioRunState.CANCELLED,
    ],
)
async def test_resume_rejects_ineligible_state_before_initializing_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget], state: ScenarioRunState
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    (
        await CentralMemory.get_memory_instance().update_scenario_run_state_async(
            scenario_result_id=str(stored.id), scenario_run_state=state
        )
    )
    with patch.object(service, "_prepare_run_blocking") as prepare:
        with pytest.raises(ScenarioRunConflictError, match="cannot resume"):
            await service.resume_run_async(scenario_result_id=str(stored.id))
        prepare.assert_not_called()


async def test_resume_missing_run_async(resume_environment: tuple[ScenarioRunService, MockPromptTarget]) -> None:
    service, _ = resume_environment
    with pytest.raises(ScenarioRunNotFoundError, match="not found"):
        await service.resume_run_async(scenario_result_id="00000000-0000-0000-0000-000000000001")


async def test_resume_rejects_missing_target_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    TargetRegistry.get_registry_singleton().instances.unregister(_TARGET_NAME)
    with pytest.raises(ValueError, match="[Tt]arget"):
        await service.resume_run_async(scenario_result_id=str(stored.id))


async def test_resume_rejects_target_drift_without_changing_saved_results_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    run_id = str(stored.id)
    changed_identifier = target.get_identifier().model_copy(update={"class_name": "ChangedMockTarget"})
    target.prompt_sent.clear()
    with patch.object(target, "get_identifier", return_value=changed_identifier):
        with pytest.raises(ValueError, match="does not match the current"):
            await service.resume_run_async(scenario_result_id=run_id)
    after = await CentralMemory.get_memory_instance().get_scenario_results_async(scenario_result_ids=[run_id])
    assert after[0].model_dump() == stored.model_dump()
    assert target.prompt_sent == []


async def test_overlapping_resume_requests_only_initialize_once_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fail_launch_async(*, request: RunScenarioRequest) -> None:
        entered.set()
        await release.wait()
        raise ValueError("Synthetic initialization failure")

    with patch.object(service, "_start_run_locked_async", side_effect=fail_launch_async) as start:
        first = asyncio.create_task(service.resume_run_async(scenario_result_id=str(stored.id)))
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(ScenarioRunConflictError, match="already being resumed"):
            await service.resume_run_async(scenario_result_id=str(stored.id))
        with pytest.raises(ScenarioRunConflictError, match="already being resumed"):
            await service.start_run_async(
                request=RunScenarioRequest(
                    scenario_name=_SCENARIO_NAME, target_name=_TARGET_NAME, scenario_result_id=str(stored.id)
                )
            )
        release.set()
        with pytest.raises(ValueError, match="Synthetic initialization"):
            await first
        assert start.await_count == 1


async def test_fresh_launch_saves_only_nonsecret_resume_inputs_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, _ = resume_environment
    with patch.object(service, "_run_initializers_async", new_callable=AsyncMock):
        response = await service.start_run_async(
            request=RunScenarioRequest(
                scenario_name=_SCENARIO_NAME,
                target_name=_TARGET_NAME,
                adversarial_target_name=_TARGET_NAME,
                include_baseline=False,
                labels=_LABELS,
                initializers=["test-initializer"],
                initializer_args={"test-initializer": {"api_key": "never-persist-this"}},
                scenario_params={"marker": "saved"},
                max_concurrency=1,
            )
        )
        await _wait_for_idle_async(service)
    stored = await CentralMemory.get_memory_instance().get_scenario_result_header_async(
        scenario_result_id=response.scenario_result_id
    )
    assert stored is not None
    saved = stored.metadata[_LAUNCH_REQUEST_METADATA_KEY]
    assert saved["target_name"] == _TARGET_NAME
    assert saved["adversarial_target_name"] == _TARGET_NAME
    assert saved["max_concurrency"] == 1
    assert saved["include_baseline"] is False
    assert not {"initializer_args", "initializers", "scenario_params", "labels"} & saved.keys()
    assert "never-persist-this" not in str(stored.metadata)


async def test_resumed_run_uses_existing_fifo_scheduler_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    run_id = str(stored.id)
    hold_active = asyncio.Event()
    entered = asyncio.Event()

    async def hold_run_async(*, scenario_result_id: str) -> None:
        entered.set()
        await hold_active.wait()

    with patch.object(service, "_execute_run_async", side_effect=hold_run_async):
        active = await service.start_run_async(
            request=RunScenarioRequest(scenario_name=_SCENARIO_NAME, target_name=_TARGET_NAME, max_concurrency=1)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        resumed = await service.resume_run_async(scenario_result_id=run_id)
        assert resumed.status == ScenarioRunState.QUEUED
        assert resumed.queue_position == 1
        assert resumed.active_scenario_result_id == active.scenario_result_id
        assert resumed.completed_at is None
        assert resumed.started_at is None
        with patch.object(service, "_prepare_run_blocking") as prepare:
            with pytest.raises(ScenarioRunConflictError, match="already scheduled"):
                await service.resume_run_async(scenario_result_id=run_id)
            prepare.assert_not_called()
        assert [entry.scenario_result_id for entry in service.get_queue_snapshot().queued] == [run_id]
        await service.cancel_run_async(scenario_result_id=run_id)
        hold_active.set()


async def test_resume_missing_scenario_registration_is_explicit_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    registry = ScenarioRegistry.get_registry_singleton()
    with patch.object(registry, "get_class", side_effect=KeyError("Scenario is no longer registered")):
        with pytest.raises(ValueError, match="no longer registered"):
            await service.resume_run_async(scenario_result_id=str(stored.id))


@pytest.mark.parametrize("missing", [name for name in _LAUNCH_REQUEST_FIELDS if name != "adversarial_target_name"])
async def test_resume_incomplete_saved_configuration_never_uses_defaults_async(
    *, resume_environment: tuple[ScenarioRunService, MockPromptTarget], missing: str
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    del stored.metadata[_LAUNCH_REQUEST_METADATA_KEY][missing]
    (
        await CentralMemory.get_memory_instance().update_scenario_metadata_async(
            scenario_result_id=str(stored.id), metadata=stored.metadata
        )
    )
    with patch.object(service, "_prepare_run_blocking") as prepare:
        with pytest.raises(ScenarioRunConflictError, match="incomplete"):
            await service.resume_run_async(scenario_result_id=str(stored.id))
        prepare.assert_not_called()


async def test_resume_older_launch_record_without_adversarial_selection_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    del stored.metadata[_LAUNCH_REQUEST_METADATA_KEY]["adversarial_target_name"]
    request = service._restore_launch_request(stored=stored)
    assert request.adversarial_target_name is None


async def test_resume_restores_selected_adversarial_target_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    adversarial = MockPromptTarget()
    TargetRegistry.get_registry_singleton().instances.register(adversarial, name="saved-adversarial")
    stored.metadata[_LAUNCH_REQUEST_METADATA_KEY]["adversarial_target_name"] = "saved-adversarial"
    (
        await CentralMemory.get_memory_instance().update_scenario_metadata_async(
            scenario_result_id=str(stored.id), metadata=stored.metadata
        )
    )
    with patch.object(service, "_enqueue_run_async", wraps=service._enqueue_run_async) as enqueue:
        await service.resume_run_async(scenario_result_id=str(stored.id))
        await _wait_for_idle_async(service)
    assert enqueue.await_args.kwargs["scheduled"].adversarial_target is adversarial


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_concurrency", None),
        ("max_concurrency", 0),
        ("max_concurrency", "1"),
        ("max_retries", None),
        ("max_retries", -1),
        ("include_baseline", None),
        ("include_baseline", "false"),
        ("scenario_name", ""),
        ("target_name", " "),
        ("adversarial_target_name", 42),
        ("techniques", "direct"),
        ("dataset_names", "dataset"),
        ("max_dataset_size", 0),
        ("dataset_filters", {"unsupported": ["value"]}),
    ],
)
async def test_resume_invalid_saved_configuration_is_rejected_before_initialization_async(
    *, resume_environment: tuple[ScenarioRunService, MockPromptTarget], field: str, value: object
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    stored.metadata[_LAUNCH_REQUEST_METADATA_KEY][field] = value
    with (
        patch.object(service._memory, "get_scenario_result_header_async", return_value=stored),
        patch.object(service, "_prepare_run_blocking") as prepare,
    ):
        with pytest.raises(ScenarioRunConflictError, match="incomplete|invalid|empty"):
            await service.resume_run_async(scenario_result_id=str(stored.id))
        prepare.assert_not_called()


@pytest.mark.parametrize("missing", ["techniques", "datasets"])
async def test_resume_missing_canonical_selection_never_uses_current_defaults_async(
    *, resume_environment: tuple[ScenarioRunService, MockPromptTarget], missing: str
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    stored.scenario_identifier = stored.scenario_identifier.model_copy(update={missing: None})
    with (
        patch.object(service._memory, "get_scenario_result_header_async", return_value=stored),
        patch.object(service, "_prepare_run_blocking") as prepare,
    ):
        with pytest.raises(ScenarioRunConflictError, match="missing techniques or datasets"):
            await service.resume_run_async(scenario_result_id=str(stored.id))
        prepare.assert_not_called()


async def test_restore_launch_request_keeps_saved_settings_and_canonical_params_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    saved = stored.metadata[_LAUNCH_REQUEST_METADATA_KEY]
    saved.update(
        adversarial_target_name="saved-adversarial",
        techniques=["direct:converter.saved"],
        dataset_names=["saved-dataset"],
        max_dataset_size=7,
        dataset_filters={"harm_categories": ["test"]},
        max_concurrency=3,
        max_retries=2,
        include_baseline=False,
        scenario_result_id="different-result-id",
        initializers=["do-not-rerun"],
        initializer_args={"do-not-rerun": {"value": "do-not-use"}},
        scenario_params={"marker": "do-not-use"},
        labels={"operation": "do-not-use"},
    )

    request = service._restore_launch_request(stored=stored)

    assert {name: getattr(request, name) for name in _LAUNCH_REQUEST_FIELDS} == {
        name: saved[name] for name in _LAUNCH_REQUEST_FIELDS
    }
    assert request.scenario_result_id == str(stored.id)
    assert request.scenario_params == {"marker": "original"}
    assert request.labels == _LABELS
    assert request.initializers is None
    assert request.initializer_args is None


async def test_explicit_start_still_resumes_older_runs_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=True)
    target.prompt_sent.clear()
    response = await service.start_run_async(
        request=RunScenarioRequest(
            scenario_result_id=str(stored.id),
            scenario_name=_SCENARIO_NAME,
            target_name=_TARGET_NAME,
            max_concurrency=1,
            max_retries=0,
            include_baseline=False,
            labels=_LABELS,
        )
    )
    await _wait_for_idle_async(service)
    assert response.scenario_result_id == str(stored.id)
    assert target.prompt_sent == [_SECOND_OBJECTIVE]
    detail = await service.get_run_async(scenario_result_id=str(stored.id))
    assert detail is not None
    assert detail.status == ScenarioRunState.COMPLETED
    assert detail.error is None
    assert len(detail.failed_attacks) == 1


@pytest.mark.parametrize("state", [ScenarioRunState.IN_PROGRESS, ScenarioRunState.QUEUED, ScenarioRunState.COMPLETED])
async def test_original_start_route_also_guards_resume_admission_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget], state: ScenarioRunState
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    (
        await CentralMemory.get_memory_instance().update_scenario_run_state_async(
            scenario_result_id=str(stored.id), scenario_run_state=state
        )
    )
    with patch.object(service, "_prepare_run_blocking") as prepare:
        with pytest.raises(ScenarioRunConflictError):
            await service.start_run_async(
                request=RunScenarioRequest(
                    scenario_name=_SCENARIO_NAME, target_name=_TARGET_NAME, scenario_result_id=str(stored.id)
                )
            )
        prepare.assert_not_called()


async def test_launch_saves_declared_baseline_default_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, _ = resume_environment
    parameters = [
        parameter
        if parameter.name != "include_baseline"
        else Parameter(name="include_baseline", description="Custom baseline default", param_type=bool, default=True)
        for parameter in _OfflineResumeScenario.supported_parameters()
    ]
    with (
        patch.object(_OfflineResumeScenario, "BASELINE_ATTACK_POLICY", BaselineAttackPolicy.Disabled),
        patch.object(_OfflineResumeScenario, "supported_parameters", return_value=parameters),
    ):
        response = await service.start_run_async(
            request=RunScenarioRequest(scenario_name=_SCENARIO_NAME, target_name=_TARGET_NAME, max_concurrency=1)
        )
        await _wait_for_idle_async(service)
    stored = await CentralMemory.get_memory_instance().get_scenario_result_header_async(
        scenario_result_id=response.scenario_result_id
    )
    assert stored is not None
    assert stored.metadata[_LAUNCH_REQUEST_METADATA_KEY]["include_baseline"] is True


async def test_resume_never_schedules_replacement_result_id_async(
    resume_environment: tuple[ScenarioRunService, MockPromptTarget],
) -> None:
    service, target = resume_environment
    stored = await _create_failed_run_async(target=target, legacy=False)
    replacement = _OfflineResumeScenario(scenario_result_id="different-result-id")
    with (
        patch.object(service, "_prepare_run_blocking", return_value=_PreparedRun(scenario=replacement)),
        patch.object(service, "_enqueue_run_async") as enqueue,
    ):
        with pytest.raises(ValueError, match="changed the saved result ID"):
            await service.resume_run_async(scenario_result_id=str(stored.id))
        enqueue.assert_not_called()
