# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Domain-neutral sweep configuration, execution, and registry lifecycle."""

import threading
from collections import Counter
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import PromptSendingAttack
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackSeedGroup,
    DatasetFetchPolicy,
    ScenarioRunSizeEstimateStatus,
    SeedObjective,
)
from pyrit.models.scenario_readiness import ScenarioConfigurationError
from pyrit.registry import AttackTechniqueRegistry, Registry, ScenarioRegistry, ScorerRegistry, TargetRegistry
from pyrit.scenario import AttackTechnique, AttackTechniqueFactory, DatasetAttackConfiguration, DatasetSource, Scenario
from pyrit.scenario.scenarios import technique
from pyrit.scenario.scenarios.technique import Sweep
from pyrit.scenario.scenarios.technique.sweep import _build_sweep_technique
from pyrit.score import SubStringScorer
from pyrit.setup.initialization import reset_setup_registries
from pyrit.setup.initializers import ScorerInitializerTags

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.fixture(autouse=True)
def catalog() -> Iterator[AttackTechniqueRegistry]:
    with patch.dict(Registry._singletons, {}, clear=True):
        _build_sweep_technique.cache_clear()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_factories(
            [
                AttackTechniqueFactory(
                    name="light_probe", attack_class=PromptSendingAttack, technique_tags=["light", "single_turn"]
                ),
                AttackTechniqueFactory(
                    name="heavy_probe", attack_class=PromptSendingAttack, technique_tags=["single_turn"]
                ),
            ]
        )
        yield registry
        reset_setup_registries()


def _configure_inline(
    *,
    scenario: Sweep,
    target: MockPromptTarget,
    techniques: list[str] | None = None,
) -> None:
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "scenario_techniques": [scenario._technique_class[name] for name in techniques] if techniques else None,
            "dataset_config": DatasetAttackConfiguration(
                seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value="Describe a blue circle.")])],
            ),
            "include_baseline": False,
        }
    )


def test_catalog_discovery_needs_no_scorer_or_default_datasets(catalog: AttackTechniqueRegistry) -> None:
    with patch.object(Scenario, "_get_default_objective_scorer", side_effect=AssertionError("Resolved a scorer")):
        registry = ScenarioRegistry.get_registry_singleton()
        assert registry.get_class("technique.sweep") is Sweep
        metadata = registry.get_registered_class_metadata("technique.sweep")
    assert metadata is not None
    assert metadata.default_datasets == ()
    assert metadata.default_techniques == ("light_probe",)
    assert set(metadata.all_techniques) == {"light_probe", "heavy_probe"}
    assert metadata.default_objective_scorer_identifier is None


async def test_missing_population_is_reported_and_rejected_before_preparation_async() -> None:
    scenario = Sweep(objective_scorer=SubStringScorer(substring="default"))
    scenario.set_params_from_args(args={"objective_target": MockPromptTarget()})
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
    assert [issue.role for issue in scenario.get_readiness().issues] == ["datasets"]
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", side_effect=AssertionError("Prepared data")),
        pytest.raises(ScenarioConfigurationError, match="no default datasets"),
    ):
        await scenario.initialize_async()


async def test_missing_techniques_are_rejected_before_preparation_async() -> None:
    reset_setup_registries()
    target = MockPromptTarget()
    scenario = Sweep(objective_scorer=SubStringScorer(substring="default"))
    _configure_inline(scenario=scenario, target=target)
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", side_effect=AssertionError("Prepared data")),
        pytest.raises(ValueError, match="requires registered attack techniques"),
    ):
        await scenario.initialize_async()
    assert target.prompt_sent == []


async def test_scorer_target_does_not_enable_refusal_fallback_async() -> None:
    TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="objective_scorer_chat")
    scenario = Sweep()
    target = MockPromptTarget()
    _configure_inline(scenario=scenario, target=target)
    with (
        patch.object(Scenario, "_get_default_objective_scorer", side_effect=AssertionError("Used fallback")),
        pytest.raises(ValueError, match="requires an explicit objective_scorer"),
    ):
        await scenario.initialize_async()
    assert target.prompt_sent == []


async def test_registered_scorer_is_used_without_a_scoring_target_async() -> None:
    scorer = SubStringScorer(substring="default")
    ScorerRegistry.get_registry_singleton().instances.register(
        scorer, name="configured", tags=[ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER]
    )
    target = MockPromptTarget()
    scenario = Sweep()
    _configure_inline(scenario=scenario, target=target)
    await scenario.initialize_async()
    await scenario.run_async()
    assert scenario._objective_scorer.get_identifier().hash == scorer.get_identifier().hash
    assert target.prompt_sent == ["Describe a blue circle."]


async def test_named_populations_share_sampling_with_baseline_and_build_off_loop_async(
    *, sqlite_instance: SQLiteMemory, catalog: AttackTechniqueRegistry
) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedObjective(value=f"{name} example {index}", dataset_name=name)
            for name in ("accuracy", "helpfulness")
            for index in range(5)
        ],
        added_by="test",
    )
    target = MockPromptTarget()
    scenario = Sweep(objective_scorer=SubStringScorer(substring="default"))
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "dataset_config": DatasetAttackConfiguration(
                sources=[DatasetSource(name=name) for name in ("accuracy", "helpfulness")],
                max_per_dataset=2,
                max_total=3,
                fetch=DatasetFetchPolicy.NEVER,
            ),
        }
    )
    factory = catalog.get_factories()["light_probe"]
    create = factory.create
    loop_thread = threading.get_ident()

    def create_off_loop(**kwargs: Any) -> AttackTechnique:
        assert threading.get_ident() != loop_thread
        return create(**kwargs)

    with patch.object(factory, "create", side_effect=create_off_loop):
        await scenario.initialize_async()
    baseline, *attacks = scenario._atomic_attacks
    assert baseline.atomic_attack_name == "baseline"
    assert len(baseline.seed_groups) == 3
    assert {attack.display_group for attack in attacks} == {"accuracy", "helpfulness"}
    assert {group.logical_id for group in baseline.seed_groups} == {
        group.logical_id for attack in attacks for group in attack.seed_groups
    }
    await scenario.run_async()
    assert Counter(target.prompt_sent) == Counter({group.objective.value: 2 for group in baseline.seed_groups})


def test_dynamic_export_refreshes_after_setup_reset() -> None:
    original = technique.SweepTechnique
    reset_setup_registries()
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
        [AttackTechniqueFactory(name="replacement", attack_class=PromptSendingAttack, technique_tags=["light"])]
    )
    refreshed = technique.SweepTechnique
    assert refreshed is not original
    assert {member.value for member in refreshed.get_all_techniques()} == {"replacement"}
