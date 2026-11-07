# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit dataset loading uses scenario selection without creating a scan."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.services.scenario_service import ScenarioService
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.models import SeedDataset, SeedObjective
from pyrit.models.catalog import ScenarioRunSizeEstimateRequest
from pyrit.models.scenario_dataset import DatasetFetchPolicy, ScenarioDatasetRequest
from pyrit.registry import ScenarioRegistry
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource, Scenario
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.score import SubStringScorer


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
