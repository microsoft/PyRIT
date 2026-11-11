# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit dataset loading uses scenario selection without creating a scan."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.services import ScenarioRunService, ScenarioService
from pyrit.backend.services.dataset_preparation_service import close_dataset_preparation_async
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.models import (
    DatasetFetchPolicy,
    ScenarioDatasetRequest,
    ScenarioRunSizeEstimateRequest,
    ScenarioRunState,
    SeedDataset,
    SeedObjective,
)
from pyrit.models.catalog import RunScenarioRequest
from pyrit.registry import ScenarioRegistry, TargetRegistry
from pyrit.scenario import AtomicAttack, DatasetAttackConfiguration, DatasetSource, Scenario, ScenarioTechnique
from pyrit.scenario.core.scenario_context import ScenarioContext
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
async def test_launch_opens_while_preload_provider_is_held_and_joins_one_fetch(*, fail: bool, cancel: bool) -> None:
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
        registry.register_class(_RunScenario, name="test.background")
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
                    scenario_name="test.background", request=ScenarioRunSizeEstimateRequest()
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
