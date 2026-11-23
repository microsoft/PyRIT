# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario-aware dataset listing, estimate validation, and REST bypass regressions."""

from collections.abc import Collection, Iterator, Sequence
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from pyrit.backend.main import app
from pyrit.backend.models import DatasetListResponse
from pyrit.backend.services import ScenarioRunService, ScenarioService, get_dataset_service
from pyrit.datasets import SeedDatasetDescriptor, SeedDatasetProvider
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackSeedGroup,
    DatasetSourceInput,
    ScenarioDatasetRequest,
    ScenarioRunSizeEstimateRequest,
    SeedObjective,
    SeedOrigin,
    SeedPrompt,
)
from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, TargetRegistry
from pyrit.scenario import AttackTechniqueFactory
from pyrit.scenario.scenarios.foundry import AssertPolicy
from pyrit.scenario.scenarios.foundry.assert_policy import _build_assert_policy_technique
from unit.assert_mocks import make_assert_config, make_assert_result
from unit.mocks import MockPromptTarget

pytestmark = pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")


@pytest.fixture
def compatibility_service() -> Iterator[ScenarioService]:
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_assert_policy_technique.cache_clear()
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
        [
            AttackTechniqueFactory(name="red_teaming", attack_class=RedTeamingAttack),
            AttackTechniqueFactory(name="prompt_sending", attack_class=PromptSendingAttack),
        ]
    )
    with patch.object(ScenarioRegistry, "_discover"):
        registry = ScenarioRegistry()
        registry.register_class(AssertPolicy, name="test.policy")
    target_registry = TargetRegistry.get_registry_singleton()
    with (
        patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
        patch.object(SeedDatasetProvider, "get_all_dataset_descriptors_async", new_callable=AsyncMock) as discovery,
        patch.object(target_registry.instances, "_registry_items", {}),
    ):
        discovery.return_value = {"provider_only": SeedDatasetDescriptor(name="provider_only")}
        target_registry.instances.register(MockPromptTarget(), name="offline")
        yield ScenarioService()
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_assert_policy_technique.cache_clear()


async def _store_imported_async(*, name: str = "imported") -> None:
    groups = make_assert_result(config=make_assert_config(group_count=4, include_adversarial_prompt=True)).seed_groups
    for group in groups:
        for seed in group.seeds:
            seed.dataset_name = name
            seed.origin = SeedOrigin.USER
            seed.source = "user-imported policy cases"
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[seed for group in groups for seed in group.seeds], added_by="test"
    )


def _request(*names: str) -> ScenarioRunSizeEstimateRequest:
    return ScenarioRunSizeEstimateRequest(
        techniques=["red_teaming"],
        target_name="offline",
        scoring_target_name="offline",
        adversarial_target_name="offline",
        datasets=ScenarioDatasetRequest(sources=[DatasetSourceInput(name=name) for name in names]) if names else None,
    )


@pytest.mark.parametrize("metadata", [None, {"assert_generation": {}}])
async def test_missing_and_malformed_policy_data_are_not_listed_or_estimated_async(
    *, compatibility_service: ScenarioService, metadata: dict[str, object] | None
) -> None:
    group = AttackSeedGroup(
        seeds=[
            SeedObjective(value="Not a valid policy group", dataset_name="invalid", metadata=metadata),
            SeedPrompt(value="A valid prompt with invalid policy context.", dataset_name="invalid"),
        ]
    )
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(seeds=group.seeds, added_by="test")
    listed = await compatibility_service.list_compatible_datasets_async(scenario_name="test.policy")
    assert listed is not None
    assert "invalid" not in {item.name for item in listed.items}
    with pytest.raises(ValueError, match="ASSERT generation metadata"):
        await compatibility_service.estimate_scenario_run_size_async(
            scenario_name="test.policy", request=_request("invalid")
        )


async def test_rest_launch_revalidates_data_changed_after_listing_async(
    *, compatibility_service: ScenarioService, compatibility_headers: dict[str, str]
) -> None:
    await _store_imported_async()
    transport = ASGITransport(app=app)
    with (
        patch("pyrit.backend.routes.scenarios.get_scenario_service", return_value=compatibility_service),
        patch("pyrit.backend.routes.scenarios.get_scenario_run_service", return_value=ScenarioRunService()),
    ):
        async with AsyncClient(transport=transport, base_url="http://test", headers=compatibility_headers) as client:
            response = await client.get("/api/scenarios/catalog/test.policy/datasets")
            assert response.status_code == 200
            assert [item["name"] for item in response.json()["items"]] == ["imported"]
            await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
                seeds=[SeedObjective(value="New malformed group", dataset_name="imported")], added_by="test"
            )
            launch = await client.post(
                "/api/scenarios/runs",
                json={"scenario_name": "test.policy", **_request("imported").model_dump(mode="json")},
            )
    assert launch.status_code == 400
    assert "ASSERT generation metadata" in launch.json()["detail"]


async def test_listing_maps_the_scenario_decision_onto_named_catalog_entries_async(
    compatibility_service: ScenarioService,
) -> None:
    await _store_imported_async()
    await _store_imported_async(name="other")
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[SeedObjective(value="Unnamed objective")], added_by="test"
    )
    calls: list[tuple[list[str], set[str]]] = []

    async def decide_async(
        self: AssertPolicy, *, dataset_names: Sequence[str], loaded_dataset_names: Collection[str]
    ) -> list[str]:
        calls.append((list(dataset_names), set(loaded_dataset_names)))
        return ["provider_only", "other"]

    dataset_service = get_dataset_service()
    with (
        patch.object(AssertPolicy, "get_compatible_dataset_names_async", decide_async),
        patch.object(dataset_service, "list_datasets_async", wraps=dataset_service.list_datasets_async) as catalog,
    ):
        result = await compatibility_service.list_compatible_datasets_async(scenario_name="test.policy")
    assert result is not None
    ((names, loaded),) = calls
    assert set(names) == {"imported", "other", "provider_only"}
    assert loaded == {"imported", "other"}
    assert [item.name for item in result.items] == [name for name in names if name in {"other", "provider_only"}]
    assert {item.name: item.loaded for item in result.items} == {"other": True, "provider_only": False}
    assert catalog.call_count == 1
    assert await CentralMemory.get_memory_instance().get_scenario_results_async() == []


async def test_listing_returns_none_for_an_unknown_scenario_async(compatibility_service: ScenarioService) -> None:
    assert await compatibility_service.list_compatible_datasets_async(scenario_name="test.missing") is None


@pytest.mark.parametrize("scenario_name", ["test.policy", "test/policy"])
async def test_eligible_route_is_one_read_only_request_with_encoded_names_async(
    *, scenario_name: str, compatibility_headers: dict[str, str]
) -> None:
    service = MagicMock(spec=ScenarioService)
    service.list_compatible_datasets_async = AsyncMock(return_value=DatasetListResponse(items=[]))
    with patch("pyrit.backend.routes.scenarios.get_scenario_service", return_value=service):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=compatibility_headers
        ) as client:
            response = await client.get(f"/api/scenarios/catalog/{quote(scenario_name, safe='')}/datasets")
    assert response.status_code == 200
    assert response.json() == {"items": []}
    service.list_compatible_datasets_async.assert_awaited_once()
    assert service.list_compatible_datasets_async.call_args.kwargs["scenario_name"] == scenario_name
