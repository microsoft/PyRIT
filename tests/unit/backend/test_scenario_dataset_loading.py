# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit dataset loading uses scenario selection without creating a scan."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.services import ScenarioRunService, ScenarioService
from pyrit.backend.services.dataset_preparation_service import close_dataset_preparation_async
from pyrit.datasets import SeedDatasetProvider
from pyrit.executor.attack import PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    DatasetFetchPolicy,
    DatasetSelectionMode,
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
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.scenarios.technique import Execute
from pyrit.scenario.scenarios.technique.execute import _build_execute_technique
from pyrit.score import SubStringScorer
from unit.async_utils import wait_for_completion_async, wait_for_event_async
from unit.mocks import MockPromptTarget


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
    ):
        result = await ScenarioService().load_datasets_async(
            scenario_name="test.load",
            request=ScenarioRunSizeEstimateRequest(datasets=ScenarioDatasetRequest(max_total=1)),
        )
    assert result is not None
    assert result.settings.max_total == 1
    assert result.items[0].name == "manual_load"
    assert result.items[0].loaded is True
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
async def test_launch_opens_while_preload_provider_is_held_and_joins_one_fetch(
    *, fail: bool, cancel: bool, fixed: bool
) -> None:
    """The actual run service and real SQLite header exist before the provider is released."""
    provider = _provider()
    entered, release = asyncio.Event(), asyncio.Event()
    dataset: SeedDataset = provider.fetch_dataset_async.return_value

    async def fetch_async(*, cache: bool = True) -> SeedDataset:
        entered.set()
        await release.wait()
        if fail:
            raise ValueError("Synthetic provider failed")
        return dataset

    provider.fetch_dataset_async.side_effect = fetch_async
    with patch.object(ScenarioRegistry, "_discover"):
        registry = ScenarioRegistry()
        registry.register_class(_FixedRunScenario if fixed else _RunScenario, name="test.background")
    target = MockPromptTarget()
    await close_dataset_preparation_async()
    run_service = ScenarioRunService()
    try:
        with (
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
            patch.object(TargetRegistry.get_registry_singleton().instances, "get", return_value=target),
            patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"manual_load": provider}),
            patch.object(SeedDatasetProvider, "get_all_dataset_names_async", return_value=["manual_load"]),
        ):
            scenario_service = ScenarioService()
            preload = asyncio.create_task(
                scenario_service.load_datasets_async(
                    scenario_name="test.background", request=ScenarioRunSizeEstimateRequest(), automatic=True
                )
            )
            await wait_for_event_async(event=entered, tasks=[preload])
            summary = await wait_for_completion_async(
                future=asyncio.create_task(
                    run_service.start_run_async(
                        request=RunScenarioRequest(
                            scenario_name="test.background",
                            target_name="offline",
                            prepare_in_background=True,
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
            provider.fetch_dataset_async.assert_awaited_once()
            active = run_service._active_tasks[summary.scenario_result_id].task
            assert active is not None
            if cancel:
                await run_service.cancel_run_async(scenario_result_id=summary.scenario_result_id)
                assert not preload.done()
                provider.fetch_dataset_async.assert_awaited_once()
            release.set()
            if fail:
                with pytest.raises(ValueError, match="Synthetic provider failed"):
                    await wait_for_completion_async(future=preload)
            else:
                await wait_for_completion_async(future=preload)
            await wait_for_completion_async(future=active)
            finished = await run_service.get_run_async(scenario_result_id=summary.scenario_result_id)
            assert finished is not None
            expected = (
                ScenarioRunState.CANCELLED
                if cancel
                else ScenarioRunState.FAILED
                if fail
                else ScenarioRunState.COMPLETED
            )
            assert finished.status is expected
            if cancel:
                assert finished.completed_attacks == 0
            elif fail:
                assert "Synthetic provider failed" in (finished.error or "")
                assert finished.completed_attacks == 0
            else:
                assert not finished.dataset_preparation_pending
                assert finished.completed_attacks == 2
            provider.fetch_dataset_async.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(preload, return_exceptions=True)
        await run_service.shutdown_async()
        await close_dataset_preparation_async()


@pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")
@pytest.mark.parametrize("policy", [DatasetFetchPolicy.NEVER, DatasetFetchPolicy.ALWAYS])
async def test_background_launch_rejects_non_missing_only_source_policies(policy: DatasetFetchPolicy) -> None:
    scenario = _RunScenario()
    scenario.set_params_from_args(
        args={
            "objective_target": MockPromptTarget(),
            "dataset_overrides": ScenarioDatasetRequest(
                sources=[{"name": "manual_load", "fetch": policy}],
            ),
        }
    )
    with pytest.raises(ValueError, match="missing-only"):
        ScenarioRunService._resolved_role_metadata(scenario, missing_only=True)
    assert await CentralMemory.get_memory_instance().get_scenario_results_async() == []


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
    await close_dataset_preparation_async()
    run_service = ScenarioRunService()
    url = "/api/scenarios/catalog/test.explicit/datasets/load" if path == "load" else "/api/scenarios/runs"
    payload = {} if path == "load" else {"scenario_name": "test.explicit", "target_name": "offline"}
    try:
        with (
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
            patch.object(TargetRegistry.get_registry_singleton().instances, "get", return_value=target),
            patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"manual_load": provider}),
            patch.object(SeedDatasetProvider, "get_all_dataset_names_async", return_value=["manual_load"]),
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
        await close_dataset_preparation_async()


@pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")
async def test_automatic_load_api_keeps_failures_until_explicit_retry(
    compatibility_headers: dict[str, str],
) -> None:
    provider = _provider()
    dataset: SeedDataset = provider.fetch_dataset_async.return_value
    provider.fetch_dataset_async.side_effect = [ValueError("Retry loading this source"), dataset]
    with patch.object(ScenarioRegistry, "_discover"):
        registry = ScenarioRegistry()
        registry.register_class(_RunScenario, name="test.automatic")
    await close_dataset_preparation_async()
    try:
        with (
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
            patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"manual_load": provider}),
            patch.object(SeedDatasetProvider, "get_all_dataset_names_async", return_value=["manual_load"]),
            patch("pyrit.backend.routes.scenarios.get_scenario_service", return_value=ScenarioService()),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", headers=compatibility_headers
            ) as client:
                url = "/api/scenarios/catalog/test.automatic/datasets/load"
                for _ in range(2):
                    response = await client.post(url, params={"automatic": True}, json={})
                    assert response.status_code == 400
                provider.fetch_dataset_async.assert_awaited_once()
                response = await client.post(url, params={"automatic": True, "retry": True}, json={})
                assert response.status_code == 200, response.text
                assert provider.fetch_dataset_async.await_count == 2
    finally:
        await close_dataset_preparation_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_real_inline_execute_launch_is_not_deferred_after_estimate_failure(
    *, compatibility_headers: dict[str, str], block_dataset_provider_lookup: AsyncMock
) -> None:
    techniques = AttackTechniqueRegistry()
    techniques.register_from_factories([AttackTechniqueFactory(name="direct", attack_class=PromptSendingAttack)])
    target = MockPromptTarget()
    _build_execute_technique.cache_clear()
    await close_dataset_preparation_async()
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
        await close_dataset_preparation_async()
        _build_execute_technique.cache_clear()
