# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Garak PromptInject scenario."""

import random
from unittest.mock import MagicMock, patch

import pytest

from pyrit.analytics.technique_analysis import compute_technique_stats
from pyrit.converter import Converter, SearchReplaceConverter
from pyrit.memory import SQLiteMemory
from pyrit.models import AttackSeedGroup, ComponentIdentifier, Message, MessagePiece, SeedObjective
from pyrit.prompt_target import PromptTarget
from pyrit.registry import ScenarioRegistry
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetConstraintError
from pyrit.scenario.garak import (  # type: ignore[ty:unresolved-import]
    PromptInject,
    PromptInjectDatasetConfiguration,
    PromptInjectTechnique,
)
from pyrit.score import SubStringScorer, TrueFalseScorer
from tests.unit.mocks import MockPromptTarget


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
    technique_converters: dict[str, list[Converter]] | None = None,
) -> None:
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "scenario_techniques": techniques,
            "goal_texts": goal_texts,
            "dataset_config": dataset_config,
            "technique_converters": technique_converters,
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
        assert scenario.VERSION == 3

    def test_required_datasets_are_template_sources(self) -> None:
        assert PromptInject.required_datasets() == ["prompt_inject_contexts", "prompt_inject_techniques"]

    def test_default_dataset_config_caps_context_goal_groups(self) -> None:
        config = PromptInject()._default_dataset_config

        assert isinstance(config, PromptInjectDatasetConfiguration)
        assert config.dataset_names == ["prompt_inject_contexts", "prompt_inject_techniques"]
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
        converter = attack.attack_technique.attack.get_request_converters()[0].converters[0]
        for group in attack.seed_groups:
            assert "{{ technique_text }}" in group.prompts[0].value
            converted = await converter.convert_async(prompt=group.prompts[0].value)
            assert "custom goal" in converted.output_text
            assert "{{ technique_text }}" not in converted.output_text
        scorer = attack.attack_technique.attack.get_attack_scoring_config().objective_scorer
        assert isinstance(scorer, SubStringScorer)
        assert scorer._substring == "custom goal"

    async def test_default_run_reuses_12_groups_for_each_technique(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()

        await _initialize_async(scenario, target=mock_objective_target)

        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 60
        assert len(_objective_values(scenario)) == 12

    @pytest.mark.parametrize(("cap", "seed"), [(3, 0), (12, 11)])
    async def test_sampling_covers_every_goal_async(
        self, mock_objective_target: PromptTarget, cap: int, seed: int
    ) -> None:
        scenario = PromptInject()
        goals = ["goal A", "goal B", "goal C"]
        config = PromptInjectDatasetConfiguration(dataset_names=PromptInject.required_datasets(), max_dataset_size=cap)

        with patch("pyrit.scenario.scenarios.garak.prompt_inject.random", random.Random(seed)):
            await _initialize_async(scenario, target=mock_objective_target, goal_texts=goals, dataset_config=config)

        assert len(scenario._atomic_attacks) == 15
        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 5 * cap
        for technique in PromptInjectTechnique.resolve(None, default=PromptInjectTechnique.default()):
            groups = [
                group
                for attack in scenario._atomic_attacks
                if attack.atomic_attack_name.startswith(f"{technique.value}__")
                for group in attack.seed_groups
            ]
            assert {group.objective.metadata["goal_text"] for group in groups} == set(goals)
            assert len({group.logical_id for group in groups}) == cap

    async def test_uncapped_configuration_uses_complete_matrix(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()

        await _initialize_async(
            scenario,
            target=mock_objective_target,
            dataset_config=PromptInjectDatasetConfiguration(
                dataset_names=PromptInject.required_datasets(),
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
                dataset_names=PromptInject.required_datasets(),
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
        for goal_index in range(2):
            print_attack = next(
                attack
                for attack in scenario._atomic_attacks
                if attack.atomic_attack_name == f"ignore_print__goal_{goal_index}"
            )
            say_attack = next(
                attack
                for attack in scenario._atomic_attacks
                if attack.atomic_attack_name == f"ignore_say__goal_{goal_index}"
            )
            assert print_attack.technique_eval_hash != say_attack.technique_eval_hash
            assert [group.logical_id for group in print_attack.seed_groups] == [
                group.logical_id for group in say_attack.seed_groups
            ]

    @pytest.mark.parametrize("goal", ["custom goal", r"literal \1 \g<1> \path", "{{ technique_text }}"])
    async def test_converters_preserve_rendered_prompts_async(
        self, mock_objective_target: PromptTarget, goal: str
    ) -> None:
        scenario = PromptInject()
        await _initialize_async(
            scenario,
            target=mock_objective_target,
            goal_texts=[goal],
            dataset_config=PromptInjectDatasetConfiguration(
                dataset_names=PromptInject.required_datasets(), max_dataset_size=None
            ),
        )

        for attack in scenario._atomic_attacks:
            technique_name = attack.atomic_attack_name.removesuffix("__goal_0")
            technique_text = scenario._technique_templates[technique_name].render_template_value(goal_text=goal)
            converter = attack.attack_technique.attack.get_request_converters()[0].converters[0]
            for group in attack.seed_groups:
                prompt = group.prompts[0]
                original_id = group.logical_id
                converted = await converter.convert_async(prompt=prompt.value)
                assert converted.output_text == prompt.render_template_value(technique_text=technique_text)
                assert group.logical_id == original_id

    async def test_caller_converters_run_after_injection_async(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()
        extra = SearchReplaceConverter(pattern="custom goal", replace="converted goal")
        await _initialize_async(
            scenario,
            target=mock_objective_target,
            techniques=[PromptInjectTechnique.IgnorePrint],
            goal_texts=["custom goal"],
            technique_converters={"ignore_print": [extra]},
        )

        attack = scenario._atomic_attacks[0]
        converters = [
            converter
            for config in attack.attack_technique.attack.get_request_converters()
            for converter in config.converters
        ]
        assert converters[1:] == [extra]
        prompt = attack.seed_groups[0].prompts[0].value
        for converter in converters:
            prompt = (await converter.convert_async(prompt=prompt)).output_text
        assert "converted goal" in prompt
        assert "custom goal" not in prompt

    async def test_persisted_statistics_keep_techniques_separate_async(self, sqlite_instance: SQLiteMemory) -> None:
        target = MockPromptTarget()
        scenario = PromptInject()
        await _initialize_async(
            scenario,
            target=target,
            techniques=[PromptInjectTechnique.IgnorePrint, PromptInjectTechnique.IgnoreSay],
            goal_texts=["custom goal"],
            dataset_config=PromptInjectDatasetConfiguration(
                dataset_names=PromptInject.required_datasets(), max_dataset_size=1
            ),
        )
        attacks = {attack.atomic_attack_name: attack for attack in scenario._atomic_attacks}
        print_attack = attacks["ignore_print__goal_0"]
        say_attack = attacks["ignore_say__goal_0"]
        expected = (
            print_attack.seed_groups[0]
            .prompts[0]
            .render_template_value(
                technique_text=scenario._technique_templates["ignore_print"].render_template_value(
                    goal_text="custom goal"
                )
            )
        )

        async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
            message = normalized_conversation[-1]
            return [
                MessagePiece(
                    role="assistant",
                    original_value="custom goal" if message.get_value() == expected else "not followed",
                    conversation_id=message.message_pieces[0].conversation_id,
                ).to_message()
            ]

        with patch.object(target, "_send_prompt_to_target_async", side_effect=respond_async) as send:
            await scenario.run_async()
        assert send.call_count == 2
        stats = compute_technique_stats(
            technique_eval_hashes=[print_attack.technique_eval_hash, say_attack.technique_eval_hash],
            memory=sqlite_instance,
        )
        assert len(stats) == 2
        assert stats[print_attack.technique_eval_hash].success_rate == 1.0
        assert stats[say_attack.technique_eval_hash].success_rate == 0.0
        assert print_attack.seed_groups[0].logical_id == say_attack.seed_groups[0].logical_id

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

    async def test_dataset_cap_smaller_than_goal_count_raises(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()
        config = PromptInjectDatasetConfiguration(
            dataset_names=PromptInject.required_datasets(),
            max_dataset_size=1,
        )

        with pytest.raises(
            DatasetConstraintError,
            match=r"max_dataset_size \(1\) must be at least the number of goal_texts \(2\)",
        ):
            await _initialize_async(
                scenario,
                target=mock_objective_target,
                goal_texts=["goal one", "goal two"],
                dataset_config=config,
            )

    async def test_custom_dataset_validator_is_preserved(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()

        def reject_dataset(_: object) -> None:
            raise DatasetConstraintError("custom validator ran")

        config = PromptInjectDatasetConfiguration(
            dataset_names=PromptInject.required_datasets(),
            validators=[reject_dataset],
        )

        with pytest.raises(DatasetConstraintError, match="custom validator ran"):
            await _initialize_async(scenario, target=mock_objective_target, dataset_config=config)

    async def test_auto_fetch_false_is_preserved_async(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()
        config = PromptInjectDatasetConfiguration(dataset_names=PromptInject.required_datasets(), auto_fetch=False)

        with patch.object(config, "_fetch_dataset_async") as fetch:
            with pytest.raises(DatasetConstraintError, match="auto_fetch is disabled"):
                await _initialize_async(scenario, target=mock_objective_target, dataset_config=config)
        fetch.assert_not_called()
        assert scenario._dataset_config is config

    async def test_unsupported_dataset_configuration_type_raises(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()
        config = DatasetAttackConfiguration(dataset_names=["prompt_inject_contexts"])

        with pytest.raises(
            DatasetConstraintError,
            match="only supports PromptInjectDatasetConfiguration",
        ):
            await _initialize_async(scenario, target=mock_objective_target, dataset_config=config)

    async def test_configuration_subclass_is_rejected_async(self, mock_objective_target: PromptTarget) -> None:
        class CustomConfiguration(PromptInjectDatasetConfiguration):
            pass

        config = CustomConfiguration(dataset_names=PromptInject.required_datasets())
        with pytest.raises(DatasetConstraintError, match="only supports PromptInjectDatasetConfiguration"):
            await _initialize_async(PromptInject(), target=mock_objective_target, dataset_config=config)

    async def test_unsupported_dataset_selection_raises(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()
        config = PromptInjectDatasetConfiguration(
            dataset_names=["prompt_inject_techniques"],
            max_dataset_size=1,
        )

        with pytest.raises(DatasetConstraintError, match="requires exactly"):
            await _initialize_async(scenario, target=mock_objective_target, dataset_config=config)

    async def test_inline_dataset_is_rejected(self, mock_objective_target: PromptTarget) -> None:
        scenario = PromptInject()
        inline_config = PromptInjectDatasetConfiguration(
            seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value="custom goal")])]
        )

        with pytest.raises(DatasetConstraintError, match="inline seeds are not supported"):
            await _initialize_async(
                scenario,
                target=mock_objective_target,
                dataset_config=inline_config,
            )


@pytest.mark.usefixtures("patch_central_database")
class TestPromptInjectDatasetSampling:
    @pytest.mark.parametrize("grouped", [False, True])
    async def test_both_resolvers_preserve_goal_coverage_async(self, grouped: bool) -> None:
        goals = ["goal A", "goal B", "goal C"]
        config = PromptInjectDatasetConfiguration(
            dataset_names=PromptInject.required_datasets(), goal_texts=goals, max_dataset_size=3
        )
        with patch("pyrit.scenario.scenarios.garak.prompt_inject.random", random.Random(0)):
            if grouped:
                by_dataset = await config.get_attack_groups_by_dataset_async()
                groups = [group for values in by_dataset.values() for group in values]
            else:
                groups = await config.get_attack_seed_groups_async()

        assert len(groups) == 3
        assert {group.objective.metadata["goal_text"] for group in groups} == set(goals)
        full = await config.get_attack_seed_groups_async(apply_sampling=False)
        assert len(full) == 105

    async def test_flat_resolver_rejects_impossible_cap_async(self) -> None:
        config = PromptInjectDatasetConfiguration(
            dataset_names=PromptInject.required_datasets(), goal_texts=["goal one", "goal two"], max_dataset_size=1
        )

        with pytest.raises(DatasetConstraintError, match="must be at least the number of goal_texts"):
            await config.get_attack_seed_groups_async()
