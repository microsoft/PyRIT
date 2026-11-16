# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Saved GUI and API launches of foundry.assert_policy resume with their judge settings."""

from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import patch

import pytest

from pyrit.backend.services import ScenarioRunService
from pyrit.backend.services.scenario_run_service import _LAUNCH_REQUEST_METADATA_KEY
from pyrit.datasets import AssertProvider
from pyrit.executor.attack import RedTeamingAttack
from pyrit.memory import CentralMemory
from pyrit.models import DatasetSourceInput, ScenarioDatasetRequest
from pyrit.models.catalog import RunScenarioRequest
from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, TargetRegistry
from pyrit.registry.resolution import reject_non_external_params
from pyrit.scenario import AttackTechniqueFactory
from pyrit.scenario.scenarios.foundry import AssertPolicy
from pyrit.scenario.scenarios.foundry.assert_policy import _build_assert_policy_technique
from unit.assert_mocks import make_assert_config, make_assert_result
from unit.async_utils import TEST_WATCHDOG_SECONDS
from unit.mocks import MockPromptTarget

pytestmark = pytest.mark.timeout(TEST_WATCHDOG_SECONDS, method="thread")

_SCENARIO_NAME = "foundry.assert_policy"
_TARGET_NAME = "assert_resume_target"
_DATASET_NAME = "assert_resume"
_JUDGE_PARAMS: dict[str, Any] = {
    "judge_presets": ["safety-extended"],
    "disabled_judge_dimensions": ["pii_leakage"],
    "judge_samples": 3,
    "objective_dimension": "harm_actionability",
}


@pytest.fixture(autouse=True)
def technique_registry() -> Iterator[None]:
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_assert_policy_technique.cache_clear()
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
        [AttackTechniqueFactory(name="red_teaming", attack_class=RedTeamingAttack, technique_tags=["multi_turn"])]
    )
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    _build_assert_policy_technique.cache_clear()


@pytest.fixture
def objective_target() -> MockPromptTarget:
    return MockPromptTarget()


@pytest.fixture
async def service(
    patch_central_database: object, objective_target: MockPromptTarget
) -> AsyncIterator[ScenarioRunService]:
    result = make_assert_result(config=make_assert_config(group_count=1))
    dataset = await AssertProvider(dataset_name=_DATASET_NAME, result=result).fetch_dataset_async()
    await CentralMemory.get_memory_instance().add_seed_datasets_to_memory_async(datasets=[dataset], added_by="test")
    with patch.object(ScenarioRegistry, "_discover"), patch.object(TargetRegistry, "_discover"):
        scenarios = ScenarioRegistry()
        scenarios.register_class(AssertPolicy, name=_SCENARIO_NAME)
        targets = TargetRegistry()
        targets.instances.register(objective_target, name=_TARGET_NAME)
        with (
            patch.object(ScenarioRegistry, "get_registry_singleton", return_value=scenarios),
            patch.object(TargetRegistry, "get_registry_singleton", return_value=targets),
        ):
            run_service = ScenarioRunService()
            try:
                yield run_service
            finally:
                await run_service.shutdown_async()


async def _initialize_async(
    *,
    objective_target: MockPromptTarget,
    judge: MockPromptTarget,
    params: dict[str, Any],
    scenario_result_id: str | None = None,
) -> AssertPolicy:
    scenario = AssertPolicy(
        adversarial_chat=MockPromptTarget(), judge_target=judge, scenario_result_id=scenario_result_id
    )
    scenario.set_params_from_args(
        args={
            "objective_target": objective_target,
            "dataset_overrides": ScenarioDatasetRequest(sources=[DatasetSourceInput(name=_DATASET_NAME)]),
            **params,
        }
    )
    await scenario.initialize_async()
    return scenario


@pytest.mark.parametrize("params", [{}, _JUDGE_PARAMS, {"objective_dimension": "overrefusal"}])
async def test_saved_launch_restores_judge_settings_async(
    service: ScenarioRunService, objective_target: MockPromptTarget, params: dict[str, Any]
) -> None:
    judge = MockPromptTarget()
    original = await _initialize_async(objective_target=objective_target, judge=judge, params=params)
    assert original._scenario_result_id is not None
    stored = await CentralMemory.get_memory_instance().get_scenario_result_header_async(
        scenario_result_id=original._scenario_result_id
    )
    assert stored is not None
    stored.metadata[_LAUNCH_REQUEST_METADATA_KEY] = RunScenarioRequest(
        scenario_name=_SCENARIO_NAME, target_name=_TARGET_NAME, max_concurrency=1, include_baseline=False
    ).model_dump(mode="json", exclude={"initializers", "initializer_args", "scenario_params", "scenario_result_id"})

    restored = service._restore_launch_request(stored=stored)

    assert restored.scenario_params is not None
    restored_judge_params = {
        name: value for name, value in restored.scenario_params.items() if name in AssertPolicy._JUDGE_PARAMETER_NAMES
    }
    assert restored_judge_params == params
    reject_non_external_params(
        params=restored.scenario_params, declared=AssertPolicy.supported_parameters(), owner=_SCENARIO_NAME
    )
    resumed = await _initialize_async(
        objective_target=objective_target,
        judge=judge,
        params={
            name: value for name, value in restored.scenario_params.items() if name != "incompatible_technique_policy"
        },
        scenario_result_id=original._scenario_result_id,
    )
    assert resumed._scenario_result_id == original._scenario_result_id


def test_api_rejects_preset_files() -> None:
    with pytest.raises(ValueError, match="judge_preset_files"):
        reject_non_external_params(
            params={"judge_preset_files": ["/etc/judge.yaml"]},
            declared=AssertPolicy.supported_parameters(),
            owner=_SCENARIO_NAME,
        )
