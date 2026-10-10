# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Adversarial target resolution validates without changing execution scopes."""

from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import SeedObjective
from pyrit.models.dataset_limit import DatasetLimit, ResolvedDatasetLimit
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.registry import TargetRegistry
from pyrit.scenario import DatasetAttackConfiguration, DatasetFetchPolicy, DatasetSource, Scenario
from pyrit.scenario.core import (
    get_default_adversarial_target,
    override_default_adversarial_target,
    scenario_target_defaults,
)
from pyrit.scenario.scenarios.adaptive.text_adaptive import TextAdaptive
from pyrit.scenario.scenarios.airt.rapid_response import RapidResponse
from pyrit.scenario.scenarios.garak.api_key import ApiKey
from pyrit.scenario.scenarios.garak.prompt_inject import PromptInject, PromptInjectDatasetConfiguration
from pyrit.score import TrueFalseScorer
from unit.mocks import MockPromptTarget


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("limit", "expected"), [(None, 20), ("", 20), ("default", 20), ("all", "all"), (7, 7)])
def test_dataset_name_override_applies_explicit_total(*, limit: DatasetLimit, expected: ResolvedDatasetLimit) -> None:
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="garak.api_key",
        scenario_class=ApiKey,
        dataset_names=ApiKey.required_datasets(),
        max_dataset_size=limit,
    )
    assert resolved["dataset_config"].max_dataset_size == expected


@pytest.mark.usefixtures("patch_central_database")
def test_dataset_overrides_preserve_subclass_state_and_default() -> None:
    scenario = PromptInject()
    original = PromptInjectDatasetConfiguration(
        dataset_names=PromptInject.required_datasets(),
        goal_texts=["first goal", "second goal"],
    )
    scenario._default_dataset_config = original
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="garak.prompt_inject",
        scenario_class=MagicMock(return_value=scenario),
        dataset_names=PromptInject.required_datasets(),
        max_dataset_size=7,
        dataset_filters={"data_types": ["text"]},
    )["dataset_config"]
    assert type(resolved) is PromptInjectDatasetConfiguration
    assert resolved._goal_texts == ["first goal", "second goal"]
    assert resolved.max_total == 7
    assert resolved.filters == {"data_types": ["text"]}
    assert original.max_total == 12
    assert original.filters == {}


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scenario_class", [RapidResponse, TextAdaptive])
@pytest.mark.parametrize("total", [10, 100, "all"])
async def test_total_override_preserves_source_limits_async(
    *, scenario_class: type[Scenario], total: int | Literal["all"]
) -> None:
    technique_class = PromptInject()._technique_class
    with (
        patch.object(Scenario, "_get_default_objective_scorer", return_value=MagicMock(spec=TrueFalseScorer)),
        patch(
            "pyrit.scenario.scenarios.airt.rapid_response._build_rapid_response_technique",
            return_value=technique_class,
        ),
        patch.object(TextAdaptive, "get_technique_class", return_value=technique_class),
    ):
        scenario = scenario_class()
    original = scenario._default_dataset_config
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="test",
        scenario_class=MagicMock(return_value=scenario),
        max_dataset_size=total,
    )["dataset_config"]
    populations = {
        name: [SeedObjective(value=f"{name}-{index}", dataset_name=name) for index in range(20)]
        for name in original.dataset_names
    }
    memory = MagicMock(spec=MemoryInterface)
    memory.get_seeds_async = AsyncMock(side_effect=lambda *, dataset_name: populations[dataset_name])
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        groups = await resolved.get_attack_groups_by_dataset_async()
    assert sum(len(population) for population in groups.values()) == (
        4 * len(populations) if total == "all" else min(total, 4 * len(populations))
    )
    assert all(len(population) <= 4 for population in groups.values())
    assert resolved.max_total == total
    assert resolved.max_per_dataset == 4
    assert original.max_total == "all"
    assert original.max_per_dataset == 4


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("total", [None, 7])
@pytest.mark.parametrize("configured_provider", [False, True], ids=["registered", "configured"])
def test_name_override_retains_source_options_and_adds_defaults_for_new_names(
    *, total: int | None, configured_provider: bool
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "retained"
    retained = DatasetSource(
        name="retained",
        max_size=2,
        fetch=DatasetFetchPolicy.NEVER,
        provider=provider if configured_provider else None,
    )
    original = DatasetAttackConfiguration(
        sources=[retained, DatasetSource(name="removed")], max_per_dataset=4, max_total=20
    )
    scenario = PromptInject()
    scenario._default_dataset_config = original
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="test",
        scenario_class=MagicMock(return_value=scenario),
        dataset_names=["new", "retained"],
        max_dataset_size=total,
    )["dataset_config"]
    assert resolved.sources == (DatasetSource(name="new"), retained)
    assert resolved.sources[1] is retained
    assert resolved.sources[1].provider is retained.provider
    assert resolved.source_limit("new") == 4
    assert resolved.source_limit("retained") == 2
    assert resolved.max_total == (20 if total is None else total)
    assert original.dataset_names == ["retained", "removed"]
    assert original.max_total == 20


@pytest.mark.usefixtures("patch_central_database")
def test_omitted_total_retains_default() -> None:
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="garak.api_key", scenario_class=ApiKey, dataset_names=ApiKey.required_datasets()
    )
    assert resolved["dataset_config"].max_total == 20


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("selection", [None, "selected"])
def test_resolve_adversarial_target_does_not_mutate_scope(selection: str | None) -> None:
    outer, selected = MockPromptTarget(), MockPromptTarget()
    registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
    registry.instances.get.return_value = selected
    with (
        override_default_adversarial_target(outer),
        patch.object(TargetRegistry, "get_registry_singleton", return_value=registry),
        patch.object(
            scenario_target_defaults,
            "_adversarial_target_override",
            wraps=scenario_target_defaults._adversarial_target_override,
        ) as scoped_default,
    ):
        result = ScenarioConfigurationResolver.resolve_adversarial_target(target_name=selection)
        assert result is (selected if selection else None)
        assert get_default_adversarial_target() is outer
        scoped_default.set.assert_not_called()
        if selection is None:
            registry.instances.get.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("selection", "message"),
    [
        ("missing", "not found"),
        ("wrong_type", "must be a PromptTarget"),
        ("single_turn", "must support multi_turn"),
    ],
)
def test_resolve_adversarial_target_rejects_invalid_selection_without_changing_scope(
    *, selection: str, message: str
) -> None:
    outer, single_turn = MockPromptTarget(), MockPromptTarget()
    single_turn.apply_capabilities(capabilities=TargetCapabilities(supports_multi_turn=False))
    targets = {"wrong_type": object(), "single_turn": single_turn}
    registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
    registry.instances.get.side_effect = targets.get
    registry.instances.get_names.return_value = list(targets)
    with (
        patch.object(TargetRegistry, "get_registry_singleton", return_value=registry),
        override_default_adversarial_target(outer),
    ):
        with pytest.raises(ValueError, match=message):
            ScenarioConfigurationResolver.resolve_adversarial_target(target_name=selection)
        assert get_default_adversarial_target() is outer
