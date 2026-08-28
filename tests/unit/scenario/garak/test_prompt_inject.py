# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Garak PromptInject scenario."""

from unittest.mock import MagicMock

import pytest

from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.registry import ScenarioRegistry
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetConstraintError
from pyrit.scenario.garak import (  # type: ignore[ty:unresolved-import]
    PromptInject,
    PromptInjectDatasetConfiguration,
    PromptInjectTechnique,
)
from pyrit.score import SubStringScorer, TrueFalseScorer


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


@pytest.fixture
def mock_objective_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return target


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.get_identifier.return_value = _mock_id("MockObjectiveScorer")
    return scorer


async def _initialize_async(
    scenario: PromptInject,
    *,
    target: PromptTarget,
    techniques: list[PromptInjectTechnique] | None = None,
    goal_texts: list[str] | None = None,
    dataset_config: DatasetAttackConfiguration | None = None,
) -> None:
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "scenario_techniques": techniques,
            "goal_texts": goal_texts,
            "dataset_config": dataset_config,
        }
    )
    await scenario.initialize_async()


def _objective_values(scenario: PromptInject) -> set[str]:
    return {group.objective.value for attack in scenario._atomic_attacks for group in attack.seed_groups}


@pytest.mark.usefixtures("patch_central_database")
class TestPromptInjectInitialization:
    def test_scenario_is_registered(self) -> None:
        assert "garak.prompt_inject" in ScenarioRegistry().get_class_names()

    def test_no_arg_construction_for_registry(self) -> None:
        scenario = PromptInject()

        assert scenario.name == "PromptInject"
        assert scenario.VERSION == 2

    def test_required_datasets_are_internal_template_sources(self) -> None:
        assert PromptInject.required_datasets() == ["promptinject_contexts"]

    def test_default_dataset_config_caps_context_goal_groups(self) -> None:
        config = PromptInject()._default_dataset_config

        assert isinstance(config, PromptInjectDatasetConfiguration)
        assert config.dataset_names == ["promptinject_contexts"]
        assert config.max_dataset_size == 12

    def test_default_technique_expands_to_all_five_forms(self) -> None:
        resolved = PromptInjectTechnique.resolve(None, default=PromptInjectTechnique.default())

        assert {technique.value for technique in resolved} == {
            "ignore_print",
            "ignore_say",
            "ignore_print_upper",
            "nevermind",
            "screaming_stop",
        }

    def test_additional_parameters_expose_default_goal_texts(self) -> None:
        parameters = {parameter.name: parameter for parameter in PromptInject.additional_parameters()}

        assert parameters["goal_texts"].param_type == list[str]
        assert parameters["goal_texts"].default == list(PromptInject.DEFAULT_GOAL_TEXTS)
        assert "prompt_cap" not in parameters
        assert "full" not in parameters
        assert "random_seed" not in parameters


@pytest.mark.usefixtures("patch_central_database")
class TestPromptInjectAtomicAttacks:
    async def test_technique_and_goal_select_independent_axes(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()

        await _initialize_async(
            scenario,
            target=mock_objective_target,
            techniques=[PromptInjectTechnique.IgnorePrint],
            goal_texts=["custom goal"],
        )

        assert len(scenario._atomic_attacks) == 1
        attack = scenario._atomic_attacks[0]
        assert attack.atomic_attack_name == "ignore_print__goal_0"
        assert attack.display_group == "custom goal"
        assert len(attack.seed_groups) == 12
        assert all("custom goal" in group.prompts[0].value for group in attack.seed_groups)
        scorer = attack.attack_technique.attack.get_attack_scoring_config().objective_scorer
        assert isinstance(scorer, SubStringScorer)
        assert scorer._substring == "custom goal"

    async def test_default_run_reuses_12_groups_for_each_technique(
        self, mock_objective_target: PromptTarget
    ) -> None:
        scenario = PromptInject()

        await _initialize_async(scenario, target=mock_objective_target)

        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 60
        assert len(_objective_values(scenario)) == 12

    async def test_uncapped_configuration_uses_complete_matrix(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()

        await _initialize_async(
            scenario,
            target=mock_objective_target,
            dataset_config=PromptInjectDatasetConfiguration(
                dataset_names=["promptinject_contexts"],
                max_dataset_size=None,
            ),
        )

        assert len(scenario._atomic_attacks) == 15
        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 525
        assert len(_objective_values(scenario)) == 105

    async def test_dataset_sample_is_reused_for_each_technique(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()

        await _initialize_async(
            scenario,
            target=mock_objective_target,
            techniques=[PromptInjectTechnique.IgnorePrint, PromptInjectTechnique.IgnoreSay],
            goal_texts=["goal one", "goal two"],
            dataset_config=PromptInjectDatasetConfiguration(
                dataset_names=["promptinject_contexts"],
                max_dataset_size=10,
            ),
        )

        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 20
        objectives_by_technique = {
            technique: {
                group.objective.value
                for attack in scenario._atomic_attacks
                if attack.atomic_attack_name.startswith(technique)
                for group in attack.seed_groups
            }
            for technique in ("ignore_print", "ignore_say")
        }
        assert objectives_by_technique["ignore_print"] == objectives_by_technique["ignore_say"]
        assert len(objectives_by_technique["ignore_print"]) == 10

    async def test_resume_replays_persisted_dataset_sample(self, mock_objective_target: PromptTarget) -> None:
        initial = PromptInject()
        await _initialize_async(initial, target=mock_objective_target)
        initial_objectives = _objective_values(initial)

        resumed = PromptInject(scenario_result_id=initial._scenario_result_id)
        await _initialize_async(resumed, target=mock_objective_target)

        assert _objective_values(resumed) == initial_objectives
        assert sum(len(attack.seed_groups) for attack in resumed._atomic_attacks) == 60

    async def test_custom_scorer_replaces_goal_scorer(
        self, mock_objective_target: PromptTarget, mock_objective_scorer: TrueFalseScorer
    ) -> None:
        scenario = PromptInject(objective_scorer=mock_objective_scorer)

        await _initialize_async(
            scenario,
            target=mock_objective_target,
            techniques=[PromptInjectTechnique.ScreamingStop],
            goal_texts=["custom goal"],
        )

        scorer = scenario._atomic_attacks[0].attack_technique.attack.get_attack_scoring_config().objective_scorer
        assert scorer is mock_objective_scorer

    @pytest.mark.parametrize(
        ("goal_texts", "message"),
        [
            ([], "goal_texts must contain non-empty strings"),
            ([""], "goal_texts must contain non-empty strings"),
            (["duplicate", "duplicate"], "goal_texts must not contain duplicate values"),
        ],
    )
    async def test_invalid_goal_texts_raise(
        self,
        mock_objective_target: PromptTarget,
        goal_texts: list[str],
        message: str,
    ) -> None:
        scenario = PromptInject()

        with pytest.raises(ValueError, match=message):
            await _initialize_async(scenario, target=mock_objective_target, goal_texts=goal_texts)

    async def test_unsupported_dataset_selection_raises(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()
        config = PromptInjectDatasetConfiguration(
            dataset_names=["promptinject_techniques"],
            max_dataset_size=1,
        )

        with pytest.raises(DatasetConstraintError, match="only supports"):
            await _initialize_async(scenario, target=mock_objective_target, dataset_config=config)

    async def test_inline_dataset_is_rejected(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()
        inline_config = DatasetAttackConfiguration(
            seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value="custom goal")])]
        )

        with pytest.raises(DatasetConstraintError, match="inline seeds are not supported"):
            await _initialize_async(
                scenario,
                target=mock_objective_target,
                dataset_config=inline_config,
            )
