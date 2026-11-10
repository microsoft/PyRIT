# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario-aware listing, explicit validation, and REST bypass regressions."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from pyrit.backend.main import app
from pyrit.backend.services import ScenarioRunService, ScenarioService
from pyrit.datasets import SeedDatasetProvider
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    AssertGroupMetadata,
    DatasetSelectionMode,
    DatasetSourceInput,
    ScenarioDatasetOptions,
    ScenarioDatasetRequest,
    ScenarioRunSizeEstimateRequest,
    SeedObjective,
    SeedOrigin,
)
from pyrit.models.dataset_compatibility import DatasetCompatibilityStatus, ScenarioDatasetCompatibility
from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, TargetRegistry
from pyrit.scenario import AttackTechniqueFactory
from pyrit.scenario.scenarios.foundry import AssertPolicy
from pyrit.scenario.scenarios.foundry.assert_policy import _build_assert_policy_technique
from unit.assert_mocks import make_assert_config, make_assert_result
from unit.mocks import MockPromptTarget

pytestmark = pytest.mark.usefixtures("patch_central_database", "block_dataset_provider_lookup")


class _RestrictedPolicy(AssertPolicy):
    DATASET_OPTIONS = ScenarioDatasetOptions(mode=DatasetSelectionMode.SUBSET, allowed_names=["imported"])


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
        registry.register_class(_RestrictedPolicy, name="test.restricted")
    target_registry = TargetRegistry.get_registry_singleton()
    with (
        patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
        patch.object(SeedDatasetProvider, "get_all_dataset_names_async", new_callable=AsyncMock) as discovery,
        patch.object(target_registry.instances, "_registry_items", {}),
    ):
        discovery.return_value = ["provider_only"]
        target_registry.instances.register(MockPromptTarget(), name="offline")
        yield ScenarioService()
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_assert_policy_technique.cache_clear()


async def _store_imported_async() -> None:
    groups = make_assert_result(config=make_assert_config(group_count=4)).seed_groups
    for group in groups:
        for seed in group.seeds:
            seed.dataset_name = "imported"
            seed.origin = SeedOrigin.USER
            seed.source = "user-imported policy cases"
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[seed for group in groups for seed in group.seeds], added_by="test"
    )


def _request(*names: str) -> ScenarioRunSizeEstimateRequest:
    return ScenarioRunSizeEstimateRequest(
        techniques=["red_teaming"],
        target_name="offline",
        adversarial_target_name="offline",
        datasets=ScenarioDatasetRequest(sources=[DatasetSourceInput(name=name) for name in names]) if names else None,
    )


async def test_candidate_listing_never_loads_seeds_or_validates_taxonomies_async(
    compatibility_service: ScenarioService,
) -> None:
    await _store_imported_async()
    memory = CentralMemory.get_memory_instance()
    with (
        patch.object(memory, "get_seeds_async", side_effect=AssertionError("Listing must not hydrate bodies.")),
        patch.object(
            AssertGroupMetadata, "from_metadata", side_effect=AssertionError("Listing must not parse policies.")
        ),
    ):
        result = await compatibility_service.assess_datasets_async(
            scenario_name="test.policy", request=_request(), validate_selected=False
        )
    assert result is not None
    items = {item.name: item for item in result.items}
    assert items["imported"].status is DatasetCompatibilityStatus.NOT_VERIFIED
    assert items["imported"].group_count == 4
    assert items["provider_only"].status is DatasetCompatibilityStatus.NOT_VERIFIED
    assert items["provider_only"].group_count is None
    assert "validator" not in result.model_dump_json()
    assert await memory.get_scenario_results_async() == []


async def test_imported_schema_valid_data_passes_regardless_of_provenance_async(
    compatibility_service: ScenarioService,
) -> None:
    await _store_imported_async()
    result = await compatibility_service.assess_datasets_async(
        scenario_name="test.policy", request=_request("imported"), validate_selected=True
    )
    assert result is not None
    assert result.selection_compatible is True
    selected = next(item for item in result.items if item.name == "imported")
    assert selected.status is DatasetCompatibilityStatus.COMPATIBLE
    assert {item.technique_name: item.usable_count for item in selected.techniques} == {
        "red_teaming": 4,
        "baseline": 4,
    }


@pytest.mark.parametrize("metadata", [None, {"assert_generation": {}}])
async def test_missing_and_malformed_policy_data_are_not_compatible_async(
    *, compatibility_service: ScenarioService, metadata: dict[str, object] | None
) -> None:
    await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
        seeds=[SeedObjective(value="Not a valid policy group", dataset_name="invalid", metadata=metadata)],
        added_by="test",
    )
    result = await compatibility_service.assess_datasets_async(
        scenario_name="test.policy", request=_request("invalid"), validate_selected=True
    )
    assert result is not None
    assert result.selection_compatible is False
    assert next(item for item in result.items if item.selected).status is DatasetCompatibilityStatus.INCOMPATIBLE
    with pytest.raises(ValueError, match="ASSERT generation metadata"):
        await compatibility_service.estimate_scenario_run_size_async(
            scenario_name="test.policy", request=_request("invalid")
        )


async def test_unloaded_provider_stays_unverified_without_a_fetch_async(compatibility_service: ScenarioService) -> None:
    result = await compatibility_service.assess_datasets_async(
        scenario_name="test.policy", request=_request("provider_only"), validate_selected=True
    )
    assert result is not None
    assert result.selection_compatible is None
    assert result.items[0].status is DatasetCompatibilityStatus.NOT_VERIFIED


async def test_candidates_respect_scenario_subset_restrictions_async(compatibility_service: ScenarioService) -> None:
    await _store_imported_async()
    result = await compatibility_service.assess_datasets_async(
        scenario_name="test.restricted", request=_request(), validate_selected=False
    )
    assert result is not None
    assert [item.name for item in result.items] == ["imported"]
    with pytest.raises(ValueError, match="Select datasets from: imported"):
        await compatibility_service.assess_datasets_async(
            scenario_name="test.restricted", request=_request("provider_only")
        )


async def test_rest_launch_revalidates_data_changed_after_preview_async(
    *, compatibility_service: ScenarioService, compatibility_headers: dict[str, str]
) -> None:
    await _store_imported_async()
    transport = ASGITransport(app=app)
    with (
        patch("pyrit.backend.routes.scenarios.get_scenario_service", return_value=compatibility_service),
        patch("pyrit.backend.routes.scenarios.get_scenario_run_service", return_value=ScenarioRunService()),
    ):
        async with AsyncClient(transport=transport, base_url="http://test", headers=compatibility_headers) as client:
            response = await client.post(
                "/api/scenarios/catalog/test.policy/datasets/assess?validate_selected=true",
                json=_request("imported").model_dump(mode="json"),
            )
            assert response.status_code == 200
            assert response.json()["selection_compatible"] is True
            await CentralMemory.get_memory_instance().add_seeds_to_memory_async(
                seeds=[SeedObjective(value="New malformed group", dataset_name="imported")], added_by="test"
            )
            launch = await client.post(
                "/api/scenarios/runs",
                json={"scenario_name": "test.policy", **_request("imported").model_dump(mode="json")},
            )
    assert launch.status_code == 400
    assert "ASSERT generation metadata" in launch.json()["detail"]


@pytest.mark.parametrize("scenario_name", ["test.policy", "test/policy"])
async def test_assessment_route_accepts_encoded_registry_names_async(
    *, scenario_name: str, compatibility_headers: dict[str, str]
) -> None:
    service = MagicMock(spec=ScenarioService)
    service.assess_datasets_async = AsyncMock(return_value=ScenarioDatasetCompatibility())
    with patch("pyrit.backend.routes.scenarios.get_scenario_service", return_value=service):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=compatibility_headers
        ) as client:
            response = await client.post(
                f"/api/scenarios/catalog/{quote(scenario_name, safe='')}/datasets/assess", json={}
            )
    assert response.status_code == 200
    assert service.assess_datasets_async.call_args.kwargs["scenario_name"] == scenario_name
