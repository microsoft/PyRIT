# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit dataset loading uses scenario selection without creating a scan."""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.services import ScenarioRunService, ScenarioService
from pyrit.datasets import SeedDatasetDescriptor, SeedDatasetProvider
from pyrit.executor.attack import PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    DatasetFetchPolicy,
    DatasetSelectionMode,
    DatasetSourceInput,
    Message,
    Parameter,
    ScenarioDatasetOptions,
    ScenarioDatasetRequest,
    ScenarioRunSizeEstimateRequest,
    ScenarioRunState,
    SeedDataset,
    SeedObjective,
)
from pyrit.models.catalog import RunScenarioRequest
from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, TargetRegistry
from pyrit.scenario import (
    AtomicAttack,
    AttackTechniqueFactory,
    DatasetAttackConfiguration,
    DatasetSource,
    Scenario,
    ScenarioTechnique,
)
from pyrit.scenario.core import dataset_configuration
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.scenarios.technique import Execute
from pyrit.scenario.scenarios.technique.execute import _build_execute_technique
from pyrit.score import SubStringScorer
from unit.async_utils import get_defined_tasks, settle_tasks_async, wait_for_completion_async, wait_for_event_async
from unit.mocks import MockPromptTarget

if TYPE_CHECKING:
    from pyrit.backend.models.datasets import ScenarioDatasetLoadResponse


class _Technique(ScenarioTechnique):
    ALL = ("all", {"all"})
    DIRECT = ("direct", set())

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _LoadScenario(Scenario):
    def __init__(
        self, *, provider: SeedDatasetProvider | None = None, fetch: DatasetFetchPolicy = DatasetFetchPolicy.NEVER
    ) -> None:
        super().__init__(
            version=1,
            technique_class=_Technique,
            objective_scorer=SubStringScorer(substring="yes"),
            default_dataset_config=DatasetAttackConfiguration(
                sources=[DatasetSource(name="manual_load", fetch=fetch, provider=provider)],
            ),
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        raise AssertionError("Loading must not build attacks")


class _RunScenario(Scenario):
    def __init__(self, *, scenario_result_id: str | None = None) -> None:
        super().__init__(
            version=1,
            technique_class=_Technique,
            objective_scorer=SubStringScorer(substring="yes"),
            default_dataset_config=DatasetAttackConfiguration(sources=[DatasetSource(name="manual_load")]),
            scenario_result_id=scenario_result_id,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        from pyrit.executor.attack import PromptSendingAttack
        from pyrit.scenario import AttackTechnique

        return [
            AtomicAttack(
                atomic_attack_name="direct",
                attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=context.objective_target)),
                seed_groups=context.seed_groups,
                memory_labels=context.memory_labels,
            )
        ]


class _FixedRunScenario(_RunScenario):
    DATASET_OPTIONS = ScenarioDatasetOptions(mode=DatasetSelectionMode.FIXED, supports_overrides=False)

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        return [
            parameter
            for parameter in super().supported_parameters()
            if parameter.name not in {"dataset_config", "dataset_overrides"}
        ]


def _provider() -> MagicMock:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "manual_load"
    provider.fetch_dataset_async = AsyncMock(
        return_value=SeedDataset(
            dataset_name="manual_load",
            seeds=[SeedObjective(value=f"Objective {i}", dataset_name="manual_load") for i in range(2)],
        )
    )
    return provider


def _registry(name: str, scenario_class: type[Scenario] = _RunScenario) -> ScenarioRegistry:
    with patch.object(ScenarioRegistry, "_discover"):
        registry = ScenarioRegistry()
        registry.register_class(scenario_class, name=name)
    return registry


async def _wait_for_run_async(run_service: ScenarioRunService, *, scenario_result_id: str) -> None:
    """Wait for a run's task, which leaves the active set once its terminal handoff finishes."""
    active = run_service._active_tasks.get(scenario_result_id)
    if active is not None:
        assert active.task is not None
        await wait_for_completion_async(future=active.task)
    snapshot = run_service.get_queue_snapshot()
    assert snapshot.active is None
    assert snapshot.queued == []


@contextmanager
def _run_patches(
    *, registry: ScenarioRegistry, target: MockPromptTarget, providers: dict[str, MagicMock]
) -> Generator[None, None, None]:
    with (
        patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
        patch.object(TargetRegistry.get_registry_singleton().instances, "get", return_value=target),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value=providers),
        patch.object(SeedDatasetProvider, "get_all_dataset_names_async", return_value=["manual_load"]),
        patch.object(
            SeedDatasetProvider,
            "get_all_dataset_descriptors_async",
            return_value={"manual_load": SeedDatasetDescriptor(name="manual_load")},
        ),
    ):
        yield


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("policy", [DatasetFetchPolicy.NEVER, DatasetFetchPolicy.ALWAYS])
async def test_manual_load_fetches_once_and_preserves_provider_and_scan_policy(policy: DatasetFetchPolicy) -> None:
    provider = _provider()
    scenario = _LoadScenario(provider=provider, fetch=policy)
    scenario.set_params_from_args(args={})
    settings = await scenario.load_datasets_async()
    await scenario.load_datasets_async()
    provider.fetch_dataset_async.assert_awaited_once()
    assert settings.sources[0].fetch is policy
    assert scenario._default_dataset_config.sources[0].provider is provider
    assert await CentralMemory.get_memory_instance().get_scenario_results_async() == []
    assert scenario._scenario_result_id is None


@pytest.mark.usefixtures("patch_central_database")
async def test_load_action_uses_registry_and_returns_full_stored_metadata() -> None:
    provider = _provider()
    with patch.object(ScenarioRegistry, "_discover"):
        registry = ScenarioRegistry()
        registry.register_class(_LoadScenario, name="test.load")
    with (
        patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"manual_load": provider}),
        patch.object(SeedDatasetProvider, "get_all_dataset_names_async", return_value=["manual_load"]),
        patch.object(
            SeedDatasetProvider,
            "get_all_dataset_descriptors_async",
            return_value={"manual_load": SeedDatasetDescriptor(name="manual_load")},
        ),
    ):
        result = await ScenarioService().load_datasets_async(
            scenario_name="test.load",
            request=ScenarioRunSizeEstimateRequest(datasets=ScenarioDatasetRequest(max_total=1)),
        )
    assert result is not None
    assert result.settings.max_total == 1
    assert result.items[0].name == "manual_load"
    assert result.items[0].loaded is True
    assert result.items[0].provider_available is True
    assert result.items[0].logical_examples == 2
    assert result.settings.sources[0].fetch is DatasetFetchPolicy.NEVER
    assert await CentralMemory.get_memory_instance().get_scenario_results_async() == []


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("policy", [DatasetFetchPolicy.NEVER, DatasetFetchPolicy.ALWAYS])
async def test_manual_load_keeps_edited_data_and_does_not_call_provider(policy: DatasetFetchPolicy) -> None:
    memory = CentralMemory.get_memory_instance()
    await memory.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="Edited objective", dataset_name="manual_load")],
        added_by="test",
    )
    provider = _provider()
    scenario = _LoadScenario(provider=provider, fetch=policy)
    await scenario.load_datasets_async()
    provider.fetch_dataset_async.assert_not_awaited()
    assert [seed.value for seed in await memory.get_seeds_async(dataset_name="manual_load")] == ["Edited objective"]


@pytest.mark.usefixtures("patch_central_database")
async def test_load_failure_surfaces_and_stores_no_partial_dataset() -> None:
    provider = _provider()
    provider.fetch_dataset_async.side_effect = RuntimeError("Provider unavailable")
    with pytest.raises(RuntimeError, match="Provider unavailable"):
        await _LoadScenario(provider=provider).load_datasets_async()
    assert await CentralMemory.get_memory_instance().get_seeds_async(dataset_name="manual_load") == []


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("error,code", [(None, 404), (ValueError("Import this dataset first"), 400)])
def test_load_route_errors_are_explicit(
    compatibility_headers: dict[str, str],
    error: Exception | None,
    code: int,
) -> None:
    with patch("pyrit.backend.routes.scenarios.get_scenario_service") as get_service:
        get_service.return_value.load_datasets_async = AsyncMock(return_value=None, side_effect=error)
        response = TestClient(app, headers=compatibility_headers).post(
            "/api/scenarios/catalog/test.load/datasets/load",
            json={},
        )
    assert response.status_code == code


@pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")
@pytest.mark.parametrize(("fail", "cancel"), [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("fixed", [False, True])
async def test_launch_opens_while_explicit_load_holds_the_dataset_writer(
    *, fail: bool, cancel: bool, fixed: bool, dataset_writer_wait: asyncio.Event
) -> None:
    """A background launch stores its run while Load fetches, then takes its turn writing the dataset."""
    provider = _provider()
    dataset: SeedDataset = provider.fetch_dataset_async.return_value
    entered, release = asyncio.Event(), asyncio.Event()
    calls = active_fetches = max_active_fetches = 0

    async def fetch_async(*, cache: bool = True) -> SeedDataset:
        nonlocal calls, active_fetches, max_active_fetches
        calls += 1
        call = calls
        active_fetches += 1
        max_active_fetches = max(max_active_fetches, active_fetches)
        try:
            entered.set()
            await release.wait()
            if fail and call == 1:
                raise ValueError("Synthetic provider failed")
            return dataset
        finally:
            active_fetches -= 1

    provider.fetch_dataset_async.side_effect = fetch_async
    registry = _registry("test.background", _FixedRunScenario if fixed else _RunScenario)
    run_service = ScenarioRunService()
    preload: asyncio.Task[ScenarioDatasetLoadResponse | None] | None = None
    try:
        with _run_patches(registry=registry, target=MockPromptTarget(), providers={"manual_load": provider}):
            preload = asyncio.create_task(
                ScenarioService().load_datasets_async(
                    scenario_name="test.background", request=ScenarioRunSizeEstimateRequest()
                )
            )
            await wait_for_event_async(event=entered, tasks=[preload])
            summary = await wait_for_completion_async(
                future=asyncio.create_task(
                    run_service.start_run_async(
                        request=RunScenarioRequest(
                            scenario_name="test.background", target_name="offline", prepare_in_background=True
                        )
                    )
                )
            )
            assert not release.is_set()
            assert not preload.done()
            assert summary.status is ScenarioRunState.IN_PROGRESS
            assert summary.dataset_preparation_pending
            assert summary.total_attacks == 0
            progress = await run_service.get_run_progress_async(
                scenario_result_id=summary.scenario_result_id, since=None, limit=20
            )
            assert progress is not None and progress.run.dataset_preparation_pending
            handle = run_service._active_tasks.get(summary.scenario_result_id)
            assert handle is not None and handle.task is not None
            active = handle.task
            # The run needs the dataset Load is still fetching, so it waits for Load's writer.
            await wait_for_event_async(event=dataset_writer_wait, tasks=[active])
            assert calls == 1
            if cancel:
                await run_service.cancel_run_async(scenario_result_id=summary.scenario_result_id)
                assert active.done()
                assert not preload.done()
            release.set()
            if fail:
                with pytest.raises(ValueError, match="Synthetic provider failed"):
                    await wait_for_completion_async(future=preload)
            else:
                assert await wait_for_completion_async(future=preload) is not None
            if not cancel:
                await wait_for_completion_async(future=active)
            finished = await run_service.get_run_async(scenario_result_id=summary.scenario_result_id)
            assert finished is not None
            if cancel:
                assert finished.status is ScenarioRunState.CANCELLED
                assert finished.dataset_preparation_pending
                assert finished.completed_attacks == 0
            else:
                # Load's failure isn't shared: the run fetches for itself once it holds the writer.
                assert finished.status is ScenarioRunState.COMPLETED, finished.error
                assert not finished.dataset_preparation_pending
                assert finished.completed_attacks == 2
            assert calls == (2 if fail else 1)
            assert max_active_fetches == 1
            assert not dataset_configuration._dataset_writers
    finally:
        release.set()
        await settle_tasks_async(*get_defined_tasks(preload))
        await run_service.shutdown_async()


@pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")
@pytest.mark.parametrize(
    ("policy", "has_provider", "message"),
    [
        (DatasetFetchPolicy.NEVER, True, "fetch is 'never'"),
        (DatasetFetchPolicy.IF_MISSING, False, "no provider"),
    ],
)
async def test_background_launch_rejects_sources_preparation_cannot_load(
    *, policy: DatasetFetchPolicy, has_provider: bool, message: str
) -> None:
    provider = _provider()
    run_service = ScenarioRunService()
    try:
        with (
            _run_patches(
                registry=_registry("test.rejected"),
                target=MockPromptTarget(),
                providers={"manual_load": provider} if has_provider else {},
            ),
            pytest.raises(ValueError, match=message),
        ):
            await run_service.start_run_async(
                request=RunScenarioRequest(
                    scenario_name="test.rejected",
                    target_name="offline",
                    datasets=ScenarioDatasetRequest(sources=[DatasetSourceInput(name="manual_load", fetch=policy)]),
                    prepare_in_background=True,
                )
            )
        provider.fetch_dataset_async.assert_not_awaited()
        assert await CentralMemory.get_memory_instance().get_scenario_results_async() == []
        snapshot = run_service.get_queue_snapshot()
        assert snapshot.active is None
        assert snapshot.queued == []
    finally:
        await run_service.shutdown_async()


@pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")
@pytest.mark.parametrize("policy", [DatasetFetchPolicy.NEVER, DatasetFetchPolicy.ALWAYS])
async def test_background_launch_honors_never_and_always_for_stored_data(policy: DatasetFetchPolicy) -> None:
    memory = CentralMemory.get_memory_instance()
    await memory.add_seeds_to_memory_async(
        seeds=[SeedObjective(value=f"Stored objective {i}", dataset_name="manual_load") for i in range(2)],
        added_by="test",
    )
    provider = _provider()
    target = MockPromptTarget()
    run_service = ScenarioRunService()
    try:
        with _run_patches(registry=_registry("test.stored"), target=target, providers={"manual_load": provider}):
            summary = await run_service.start_run_async(
                request=RunScenarioRequest(
                    scenario_name="test.stored",
                    target_name="offline",
                    datasets=ScenarioDatasetRequest(sources=[DatasetSourceInput(name="manual_load", fetch=policy)]),
                    include_baseline=False,
                    prepare_in_background=True,
                )
            )
            await _wait_for_run_async(run_service, scenario_result_id=summary.scenario_result_id)
        finished = await run_service.get_run_async(scenario_result_id=summary.scenario_result_id)
        assert finished is not None
        assert finished.status is ScenarioRunState.COMPLETED, finished.error
        assert not finished.dataset_preparation_pending
        if policy is DatasetFetchPolicy.ALWAYS:
            provider.fetch_dataset_async.assert_awaited_once_with(cache=False)
            expected = ["Objective 0", "Objective 1"]
        else:
            provider.fetch_dataset_async.assert_not_awaited()
            expected = ["Stored objective 0", "Stored objective 1"]
        assert sorted(seed.value for seed in await memory.get_seeds_async(dataset_name="manual_load")) == expected
        assert sorted(target.prompt_sent) == expected
    finally:
        await run_service.shutdown_async()


@pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")
@pytest.mark.parametrize("path", ["load", "run"])
async def test_explicit_api_requests_retry_a_transient_provider_failure(
    *, path: str, compatibility_headers: dict[str, str]
) -> None:
    provider = _provider()
    dataset: SeedDataset = provider.fetch_dataset_async.return_value
    provider.fetch_dataset_async.side_effect = [ValueError("Transient provider failure"), dataset]
    with patch.object(ScenarioRegistry, "_discover"):
        registry = ScenarioRegistry()
        registry.register_class(_RunScenario, name="test.explicit")
    target = MockPromptTarget()
    run_service = ScenarioRunService()
    url = "/api/scenarios/catalog/test.explicit/datasets/load" if path == "load" else "/api/scenarios/runs"
    payload = {} if path == "load" else {"scenario_name": "test.explicit", "target_name": "offline"}
    try:
        with (
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
            patch.object(TargetRegistry.get_registry_singleton().instances, "get", return_value=target),
            patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"manual_load": provider}),
            patch.object(SeedDatasetProvider, "get_all_dataset_names_async", return_value=["manual_load"]),
            patch.object(
                SeedDatasetProvider,
                "get_all_dataset_descriptors_async",
                return_value={"manual_load": SeedDatasetDescriptor(name="manual_load")},
            ),
            patch("pyrit.backend.routes.scenarios.get_scenario_service", return_value=ScenarioService()),
            patch("pyrit.backend.routes.scenarios.get_scenario_run_service", return_value=run_service),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=compatibility_headers
            ) as client:
                first = await client.post(url, json=payload)
                assert first.status_code == 400
                assert "Transient provider failure" in first.text
                second = await client.post(url, json=payload)
                assert second.status_code == (200 if path == "load" else 202), second.text
            assert provider.fetch_dataset_async.await_count == 2
    finally:
        await run_service.shutdown_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_real_inline_execute_launch_is_not_deferred_after_estimate_failure(
    *, compatibility_headers: dict[str, str], block_dataset_provider_lookup: AsyncMock
) -> None:
    techniques = AttackTechniqueRegistry()
    techniques.register_from_factories([AttackTechniqueFactory(name="direct", attack_class=PromptSendingAttack)])
    target = MockPromptTarget()
    _build_execute_technique.cache_clear()
    runs = ScenarioRunService()
    try:
        with (
            patch.object(AttackTechniqueRegistry, "get_registry_singleton", return_value=techniques),
            patch.object(Execute, "_get_default_objective_scorer", return_value=SubStringScorer(substring="hello")),
            patch.object(TargetRegistry.get_registry_singleton().instances, "get", return_value=target),
            patch.object(ScenarioRegistry, "_discover"),
            patch.object(Execute, "create_pending_run_async", new_callable=AsyncMock) as pending,
        ):
            registry = ScenarioRegistry()
            registry.register_class(Execute, name="technique.execute")
            with (
                patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
                patch("pyrit.backend.routes.scenarios.get_scenario_run_service", return_value=runs),
                patch("pyrit.backend.routes.scenarios.get_scenario_service", side_effect=ScenarioService),
                patch.object(
                    ScenarioService,
                    "estimate_scenario_run_size_async",
                    new_callable=AsyncMock,
                    side_effect=HTTPException(status_code=503, detail="Estimate temporarily unavailable"),
                ),
            ):
                metadata = registry.get_class_metadata(Execute)
                assert "dataset_config" not in {parameter.name for parameter in metadata.supported_parameters}
                assert metadata.default_datasets == () and metadata.dataset_defaults.sources == []
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test", headers=compatibility_headers
                ) as client:
                    estimate = await client.post("/api/scenarios/catalog/technique.execute/estimate", json={})
                    assert estimate.status_code == 503
                    response = await client.post(
                        "/api/scenarios/runs",
                        json={
                            "scenario_name": "technique.execute",
                            "target_name": "offline",
                            "techniques": ["direct"],
                            "scenario_params": {"objective": "Write a greeting."},
                        },
                    )
                    assert response.status_code == 202, response.text
                summary = response.json()
                assert summary["dataset_preparation_pending"] is False
                pending.assert_not_awaited()
                active = runs._active_tasks.get(summary["scenario_result_id"])
                if active is not None and active.task is not None:
                    await wait_for_completion_async(future=active.task)
                progress = await runs.get_run_progress_async(
                    scenario_result_id=summary["scenario_result_id"], since=None, limit=10
                )
                assert progress is not None and not progress.run.dataset_preparation_pending
                assert progress.run.status is ScenarioRunState.COMPLETED
                assert target.prompt_sent == ["Write a greeting."]
                block_dataset_provider_lookup.assert_not_awaited()
    finally:
        await runs.shutdown_async()
        _build_execute_technique.cache_clear()


@pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")
async def test_pending_header_progress_delivers_complete_plan() -> None:
    """A progress read that starts while datasets load leaves the client's cursor where the plan comes next."""
    provider = _provider()
    dataset: SeedDataset = provider.fetch_dataset_async.return_value
    fetch_entered, release_fetch = asyncio.Event(), asyncio.Event()
    second_send_entered, release_second_send = asyncio.Event(), asyncio.Event()

    async def fetch_async(*, cache: bool = True) -> SeedDataset:
        fetch_entered.set()
        await release_fetch.wait()
        return dataset

    provider.fetch_dataset_async.side_effect = fetch_async

    class _SecondSendHeldTarget(MockPromptTarget):
        async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
            if self.prompt_sent:
                second_send_entered.set()
                await release_second_send.wait()
            return await super()._send_prompt_to_target_async(normalized_conversation=normalized_conversation)

    run_service = ScenarioRunService()
    try:
        with _run_patches(
            registry=_registry("test.race"), target=_SecondSendHeldTarget(), providers={"manual_load": provider}
        ):
            summary = await wait_for_completion_async(
                future=asyncio.create_task(
                    run_service.start_run_async(
                        request=RunScenarioRequest(
                            scenario_name="test.race",
                            target_name="offline",
                            prepare_in_background=True,
                            max_concurrency=1,
                            include_baseline=False,
                        )
                    )
                )
            )
            assert summary.dataset_preparation_pending
            handle = run_service._active_tasks.get(summary.scenario_result_id)
            assert handle is not None and handle.task is not None
            active = handle.task
            await wait_for_event_async(event=fetch_entered, tasks=[active])
            read_snapshot = run_service._progress_read_model.get_snapshot_async

            async def snapshot_after_preparation_async(**kwargs: Any) -> Any:
                # The header was read while preparing. Let preparation save the plan and one result first.
                release_fetch.set()
                await wait_for_event_async(event=second_send_entered, tasks=[active])
                return await read_snapshot(**kwargs)

            with patch.object(
                run_service._progress_read_model, "get_snapshot_async", new=snapshot_after_preparation_async
            ):
                first = await run_service.get_run_progress_async(
                    scenario_result_id=summary.scenario_result_id, since=None, limit=20
                )
            assert first is not None
            assert first.run.dataset_preparation_pending
            assert first.plan is None
            assert first.next_cursor is None
            release_second_send.set()
            await wait_for_completion_async(future=active)
            second = await run_service.get_run_progress_async(
                scenario_result_id=summary.scenario_result_id, since=first.next_cursor, limit=20
            )
        assert second is not None
        assert second.plan_complete
        assert second.plan is not None
        planned = {
            (group.id, seed_group_id) for group in second.plan.atomic_groups for seed_group_id in group.seed_group_ids
        }
        assert len(planned) == 2
    finally:
        release_fetch.set()
        release_second_send.set()
        await run_service.shutdown_async()


@pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")
async def test_history_marks_failed_preparation_not_resumable() -> None:
    """History shows that a run whose datasets never loaded can't resume, as its detail does."""
    provider = _provider()
    provider.fetch_dataset_async.side_effect = ValueError("Synthetic provider failed")
    run_service = ScenarioRunService()
    try:
        with _run_patches(
            registry=_registry("test.history"), target=MockPromptTarget(), providers={"manual_load": provider}
        ):
            summary = await run_service.start_run_async(
                request=RunScenarioRequest(
                    scenario_name="test.history", target_name="offline", prepare_in_background=True
                )
            )
            await _wait_for_run_async(run_service, scenario_result_id=summary.scenario_result_id)
            page = await run_service.list_runs_async()
            item = next(item for item in page.items if item.scenario_result_id == summary.scenario_result_id)
            assert item.status is ScenarioRunState.FAILED
            assert item.dataset_preparation_pending
            detail = await run_service.get_run_async(scenario_result_id=summary.scenario_result_id)
            assert detail is not None
            assert detail.dataset_preparation_pending
            assert "Synthetic provider failed" in (detail.error or "")
            with pytest.raises(ValueError, match="never finished dataset preparation"):
                await run_service.resume_run_async(scenario_result_id=summary.scenario_result_id)
    finally:
        await run_service.shutdown_async()
