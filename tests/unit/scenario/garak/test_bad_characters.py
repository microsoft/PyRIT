# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Garak Bad Characters scenario."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.converter import (
    BackspaceConverter,
    BidiSwapConverter,
    Converter,
    UnicodeConfusableConverter,
    ZeroWidthConverter,
)
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import AttackSeedGroup, ComponentIdentifier, Message, MessagePiece, SeedObjective
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.registry import ScenarioRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.core import AtomicAttack
from pyrit.scenario.garak import BadCharacters, BadCharactersTechnique  # type: ignore[ty:import-not-found]
from pyrit.scenario.scenarios.garak.bad_characters import DEFAULT_MAX_DATASET_SIZE
from pyrit.score import TrueFalseScorer

_TECHNIQUE_CONVERTERS: dict[str, type[Converter]] = {
    "invisible": ZeroWidthConverter,
    "homoglyph": UnicodeConfusableConverter,
    "reordering": BidiSwapConverter,
    "deletion": BackspaceConverter,
}


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


@pytest.fixture
def mock_objective_target() -> MagicMock:
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_id("MockObjectiveScorer")
    return mock


def _groups() -> list[AttackSeedGroup]:
    return [
        AttackSeedGroup(seeds=[SeedObjective(value="Explain how to perform a harmful task")]),
        AttackSeedGroup(seeds=[SeedObjective(value="Write instructions for something dangerous")]),
    ]


async def _initialize(
    *,
    scenario: BadCharacters,
    target: PromptTarget,
    techniques: list[BadCharactersTechnique] | None = None,
    include_baseline: bool | None = None,
    memory_labels: dict[str, str] | None = None,
    perturbation_budget: Any = None,
) -> None:
    args: dict[str, Any] = {
        "objective_target": target,
        "dataset_config": DatasetAttackConfiguration(seed_groups=_groups()),
    }
    if techniques is not None:
        args["scenario_techniques"] = techniques
    if include_baseline is not None:
        args["include_baseline"] = include_baseline
    if memory_labels is not None:
        args["memory_labels"] = memory_labels
    if perturbation_budget is not None:
        args["perturbation_budget"] = perturbation_budget

    with patch("pyrit.prompt_target.common.target_requirements.TargetRequirements.validate"):
        scenario.set_params_from_args(args=args)
        await scenario.initialize_async()


def _converters(atomic_attack: AtomicAttack) -> list[Converter]:
    return [
        converter
        for configuration in atomic_attack.attack_technique.attack.get_request_converters()
        for converter in configuration.converters
    ]


@pytest.mark.usefixtures("patch_central_database")
class TestBadCharactersInitialization:
    def test_no_arg_instantiation(self, mock_objective_scorer):
        with patch.object(BadCharacters, "_get_default_objective_scorer", return_value=mock_objective_scorer):
            scenario = BadCharacters()

        assert scenario.name == "BadCharacters"
        assert scenario.VERSION == 1

    def test_custom_objective_scorer_is_used(self, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        assert scenario._objective_scorer is mock_objective_scorer

    def test_default_dataset_is_capped_harmbench(self, mock_objective_scorer):
        config = BadCharacters(objective_scorer=mock_objective_scorer)._default_dataset_config

        assert config.dataset_names == ["harmbench"]
        assert config.max_total == DEFAULT_MAX_DATASET_SIZE

    def test_task_achieved_rubric_is_used(self):
        expected = SCORER_SEED_PROMPT_PATH / "true_false_question" / "task_achieved.yaml"

        assert BadCharacters._get_additional_scoring_questions() == [expected]

    def test_registry_metadata(self, mock_objective_scorer):
        with patch.object(BadCharacters, "_get_default_objective_scorer", return_value=mock_objective_scorer):
            metadata = ScenarioRegistry().get_class_metadata(BadCharacters)

        assert metadata.registry_name == "garak.bad_characters"
        assert metadata.default_datasets == ("harmbench",)

    def test_technique_converters_are_not_a_parameter(self):
        parameter_names = {parameter.name for parameter in BadCharacters.supported_parameters()}

        assert "technique_converters" not in parameter_names

    def test_default_technique_is_all(self):
        assert BadCharactersTechnique.default() is BadCharactersTechnique.ALL

    def test_perturbation_budget_is_a_run_parameter(self):
        parameters = {parameter.name: parameter for parameter in BadCharacters.supported_parameters()}

        assert parameters["perturbation_budget"].param_type is int
        assert parameters["perturbation_budget"].default == 1

    @pytest.mark.parametrize(
        ("budget", "message"),
        [
            (0, "perturbation_budget must be a positive integer"),
            (-1, "perturbation_budget must be a positive integer"),
            (True, "expects int but received a bool"),
        ],
    )
    async def test_invalid_perturbation_budget_raises(
        self, budget, message, mock_objective_target, mock_objective_scorer
    ):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match=message):
            await _initialize(scenario=scenario, target=mock_objective_target, perturbation_budget=budget)


@pytest.mark.usefixtures("patch_central_database")
class TestBadCharactersAttacks:
    async def test_default_run_has_baseline_then_every_technique(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(scenario=scenario, target=mock_objective_target)

        names = [atomic_attack.atomic_attack_name for atomic_attack in scenario._atomic_attacks]
        assert names == ["baseline", "invisible", "homoglyph", "reordering", "deletion"]

    async def test_each_technique_applies_its_converter(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(scenario=scenario, target=mock_objective_target, include_baseline=False)

        for atomic_attack in scenario._atomic_attacks:
            assert isinstance(atomic_attack.attack_technique.attack, PromptSendingAttack)
            assert atomic_attack.display_group == atomic_attack.atomic_attack_name
            converters = _converters(atomic_attack)
            assert len(converters) == 1
            assert type(converters[0]) is _TECHNIQUE_CONVERTERS[atomic_attack.atomic_attack_name]
            assert converters[0]._budget == 1

    async def test_invisible_uses_all_three_zero_width_characters(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(
            scenario=scenario,
            target=mock_objective_target,
            techniques=[BadCharactersTechnique.Invisible],
            include_baseline=False,
        )

        converter = _converters(scenario._atomic_attacks[0])[0]
        assert converter._characters == (
            ZeroWidthConverter.ZERO_WIDTH_SPACE,
            ZeroWidthConverter.ZERO_WIDTH_NON_JOINER,
            ZeroWidthConverter.ZERO_WIDTH_JOINER,
        )

    async def test_perturbation_budget_reaches_every_converter(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(
            scenario=scenario, target=mock_objective_target, include_baseline=False, perturbation_budget=3
        )

        assert {_converters(atomic_attack)[0]._budget for atomic_attack in scenario._atomic_attacks} == {3}

    async def test_perturbation_budget_is_recorded_in_identity(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(scenario=scenario, target=mock_objective_target, perturbation_budget=3)

        assert scenario._get_identity_params()["perturbation_budget"] == 3

    async def test_perturbations_apply_only_to_text_pieces(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(scenario=scenario, target=mock_objective_target, include_baseline=False)

        for atomic_attack in scenario._atomic_attacks:
            configurations = atomic_attack.attack_technique.attack.get_request_converters()
            assert [configuration.prompt_data_types_to_apply for configuration in configurations] == [["text"]]

    async def test_selected_techniques_only(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(
            scenario=scenario,
            target=mock_objective_target,
            techniques=[BadCharactersTechnique.Deletion, BadCharactersTechnique.Homoglyph],
            include_baseline=False,
        )

        assert {atomic_attack.atomic_attack_name for atomic_attack in scenario._atomic_attacks} == {
            "deletion",
            "homoglyph",
        }

    async def test_baseline_and_techniques_share_seed_groups(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(scenario=scenario, target=mock_objective_target)

        objectives = [
            [group.objective.value for group in atomic_attack.seed_groups] for atomic_attack in scenario._atomic_attacks
        ]
        assert all(entry == objectives[0] for entry in objectives)
        assert len(objectives[0]) == 2

    async def test_memory_labels_reach_every_attack(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(scenario=scenario, target=mock_objective_target, memory_labels={"run": "bad-characters"})

        assert all(
            atomic_attack._memory_labels == {"run": "bad-characters"} for atomic_attack in scenario._atomic_attacks
        )

    async def test_every_attack_uses_the_objective_scorer(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        await _initialize(scenario=scenario, target=mock_objective_target)

        for atomic_attack in scenario._atomic_attacks:
            scoring_config = atomic_attack.attack_technique.attack.get_attack_scoring_config()
            assert scoring_config.objective_scorer is mock_objective_scorer

    async def test_attachments_pass_through_while_text_is_perturbed(self, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)
        await _initialize(scenario=scenario, target=mock_objective_target, include_baseline=False)
        text = "Explain how to perform a harmful task"

        for atomic_attack in scenario._atomic_attacks:
            message = Message(
                message_pieces=[
                    MessagePiece(role="user", original_value=text, conversation_id="conversation"),
                    MessagePiece(
                        role="user",
                        original_value="/tmp/attachment.png",
                        original_value_data_type="image_path",
                        conversation_id="conversation",
                    ),
                ]
            )

            await PromptNormalizer().convert_values_async(
                converter_configurations=atomic_attack.attack_technique.attack.get_request_converters(),
                message=message,
            )

            text_piece, image_piece = message.message_pieces
            assert text_piece.converted_value != text
            assert image_piece.converted_value == "/tmp/attachment.png"
            assert image_piece.converted_value_data_type == "image_path"

    @pytest.mark.parametrize("technique", ["invisible", "homoglyph", "reordering", "deletion"])
    async def test_converter_changes_the_objective_text(self, technique, mock_objective_target, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)
        prompt = "Explain how to perform a harmful task"

        converter = scenario._converter_for(technique=technique)
        result = await converter.convert_async(prompt=prompt)

        assert result.output_text != prompt

    def test_unknown_technique_raises(self, mock_objective_scorer):
        scenario = BadCharacters(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match="Unknown Bad Characters technique"):
            scenario._converter_for(technique="all")
