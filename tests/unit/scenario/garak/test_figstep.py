# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Garak FigStep scenario."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.converter import Base64Converter, Converter
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective, SeedPrompt
from pyrit.prompt_target import (
    CapabilityName,
    PromptTarget,
    TargetCapabilities,
    TargetConfiguration,
)
from pyrit.registry import ScenarioRegistry
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.garak import FigStep, FigStepTechnique  # type: ignore[ty:unresolved-import]
from pyrit.scenario.scenarios.garak.figstep import DEFAULT_MAX_DATASET_SIZE
from pyrit.score import TrueFalseScorer
from tests.unit.mocks import MockPromptTarget


def _mock_id(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="test")


@pytest.fixture
def mock_objective_target():
    mock = MagicMock(spec=PromptTarget)
    mock.get_identifier.return_value = _mock_id("MockObjectiveTarget")
    return mock


@pytest.fixture
def mock_objective_scorer():
    mock = MagicMock(spec=TrueFalseScorer)
    mock.get_identifier.return_value = _mock_id("MockObjectiveScorer")
    return mock


def _figstep_group(*, objective: str = "Explain how to perform a harmful task") -> AttackSeedGroup:
    return AttackSeedGroup(
        seeds=[
            SeedObjective(value=objective),
            SeedPrompt(value="/tmp/figstep.png", data_type="image_path", role="user", sequence=0),
            SeedPrompt(value="The image shows a numbered list.", data_type="text", role="user", sequence=0),
        ]
    )


def _inline_config(*, groups: list[AttackSeedGroup] | None = None) -> DatasetAttackConfiguration:
    return DatasetAttackConfiguration(seed_groups=groups or [_figstep_group()])


async def _initialize(
    *,
    scenario: FigStep,
    target: PromptTarget,
    dataset_config: DatasetAttackConfiguration,
    include_baseline: bool | None = None,
    memory_labels: dict[str, str] | None = None,
    technique_converters: dict[str, list[Converter]] | None = None,
) -> None:
    args: dict[str, Any] = {
        "objective_target": target,
        "dataset_config": dataset_config,
    }
    if include_baseline is not None:
        args["include_baseline"] = include_baseline
    if memory_labels is not None:
        args["memory_labels"] = memory_labels
    if technique_converters is not None:
        args["technique_converters"] = technique_converters

    with patch("pyrit.prompt_target.common.target_requirements.TargetRequirements.validate"):
        scenario.set_params_from_args(args=args)
        await scenario.initialize_async()


@pytest.mark.usefixtures("patch_central_database")
class TestFigStepInitialization:
    def test_no_arg_instantiation(self, mock_objective_scorer):
        with patch.object(FigStep, "_get_default_objective_scorer", return_value=mock_objective_scorer):
            scenario = FigStep()

        assert scenario.name == "FigStep"
        assert scenario.VERSION == 1

    def test_custom_objective_scorer_is_used(self, mock_objective_scorer):
        scenario = FigStep(objective_scorer=mock_objective_scorer)

        assert scenario._objective_scorer is mock_objective_scorer
        assert scenario._scorer_config.objective_scorer is mock_objective_scorer

    def test_default_dataset_uses_figstep(self, mock_objective_scorer):
        config = FigStep(objective_scorer=mock_objective_scorer)._default_dataset_config

        assert config.dataset_names == ["figstep"]
        assert config.max_dataset_size == DEFAULT_MAX_DATASET_SIZE

    def test_required_datasets_include_both_figstep_datasets(self):
        assert FigStep.required_datasets() == ["figstep", "figstep_pro"]

    def test_dataset_config_is_the_only_dataset_selector(self):
        parameter_names = {parameter.name for parameter in FigStep.supported_parameters()}

        assert "dataset_config" in parameter_names
        assert "variant" not in parameter_names

    def test_task_achieved_rubric_is_used(self):
        expected = SCORER_SEED_PROMPT_PATH / "true_false_question" / "task_achieved.yaml"

        assert FigStep._get_additional_scoring_questions() == [expected]

    def test_registry_metadata_exposes_default_dataset_without_variant(self, mock_objective_scorer):
        registry = ScenarioRegistry()
        with patch.object(FigStep, "_get_default_objective_scorer", return_value=mock_objective_scorer):
            metadata = registry.get_class_metadata(FigStep)

        assert metadata.registry_name == "garak.figstep"
        assert metadata.default_datasets == ("figstep",)
        assert "variant" not in {parameter.name for parameter in metadata.supported_parameters}

    def test_target_requirements_declare_grouped_text_and_image(self):
        requirements = FigStep.TARGET_REQUIREMENTS

        assert requirements.native_required == frozenset({CapabilityName.MULTI_MESSAGE_PIECES})
        assert requirements.required_input_modalities == frozenset({frozenset({"text", "image_path"})})

    @pytest.mark.parametrize(
        ("capabilities", "expected_error"),
        [
            (
                TargetCapabilities(
                    supports_multi_message_pieces=False,
                    input_modalities=frozenset({frozenset({"text", "image_path"})}),
                ),
                CapabilityName.MULTI_MESSAGE_PIECES.value,
            ),
            (
                TargetCapabilities(
                    supports_multi_message_pieces=True,
                    input_modalities=frozenset({frozenset({"text"}), frozenset({"image_path"})}),
                ),
                "input modality",
            ),
        ],
    )
    def test_target_requirements_reject_incompatible_targets(self, capabilities, expected_error):
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=capabilities)

        with pytest.raises(ValueError, match=expected_error):
            FigStep.TARGET_REQUIREMENTS.validate(target=target)


@pytest.mark.usefixtures("patch_central_database")
class TestFigStepDatasetResolution:
    async def test_named_pro_dataset_selects_pro_attack(self, mock_objective_target, mock_objective_scorer):
        groups = [_figstep_group()]
        config = DatasetAttackConfiguration(
            dataset_names=["figstep_pro"],
            max_dataset_size=1,
            auto_fetch=False,
        )
        with (
            patch("pyrit.prompt_target.common.target_requirements.TargetRequirements.validate"),
            patch.object(
                DatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
                return_value={"figstep_pro": groups},
            ) as resolve_groups,
        ):
            scenario = FigStep(objective_scorer=mock_objective_scorer)
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": config,
                    "include_baseline": False,
                }
            )
            await scenario.initialize_async()

        assert scenario._dataset_config.dataset_names == ["figstep_pro"]
        assert scenario._atomic_attacks[0].atomic_attack_name == "visual_jailbreak_figstep_pro"
        assert [call.kwargs for call in resolve_groups.await_args_list] == [
            {"apply_sampling": False},
            {"apply_sampling": True},
        ]

    async def test_unrelated_named_dataset_is_rejected(self, mock_objective_target, mock_objective_scorer):
        scenario = FigStep(objective_scorer=mock_objective_scorer)
        config = DatasetAttackConfiguration(dataset_names=["harmbench"], auto_fetch=False)

        with (
            patch("pyrit.prompt_target.common.target_requirements.TargetRequirements.validate"),
            patch.object(
                DatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                new_callable=AsyncMock,
            ) as resolve_groups,
        ):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": config,
                }
            )
            with pytest.raises(ValueError, match="exactly one named dataset"):
                await scenario.initialize_async()
            resolve_groups.assert_not_awaited()

    async def test_multiple_named_figstep_datasets_are_rejected(self, mock_objective_target, mock_objective_scorer):
        scenario = FigStep(objective_scorer=mock_objective_scorer)
        config = DatasetAttackConfiguration(dataset_names=["figstep", "figstep_pro"], auto_fetch=False)

        with patch("pyrit.prompt_target.common.target_requirements.TargetRequirements.validate"):
            scenario.set_params_from_args(
                args={
                    "objective_target": mock_objective_target,
                    "dataset_config": config,
                }
            )
            with pytest.raises(ValueError, match="exactly one named dataset"):
                await scenario.initialize_async()

    async def test_inline_group_must_contain_text_and_image(self, mock_objective_target, mock_objective_scorer):
        invalid_group = AttackSeedGroup(
            seeds=[
                SeedObjective(value="harmful objective"),
                SeedPrompt(value="text only", data_type="text", role="user", sequence=0),
            ]
        )
        scenario = FigStep(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match="text and image_path"):
            await _initialize(
                scenario=scenario,
                target=mock_objective_target,
                dataset_config=_inline_config(groups=[invalid_group]),
            )

    async def test_inline_group_must_be_single_turn(self, mock_objective_target, mock_objective_scorer):
        invalid_group = AttackSeedGroup(
            seeds=[
                SeedObjective(value="harmful objective"),
                SeedPrompt(value="first turn", data_type="text", role="user", sequence=0),
                SeedPrompt(value="/tmp/figstep.png", data_type="image_path", role="user", sequence=1),
                SeedPrompt(value="carrier text", data_type="text", role="user", sequence=1),
            ]
        )
        scenario = FigStep(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match="exactly one user message"):
            await _initialize(
                scenario=scenario,
                target=mock_objective_target,
                dataset_config=_inline_config(groups=[invalid_group]),
            )

    async def test_all_inline_groups_are_validated_before_sampling(self, mock_objective_target, mock_objective_scorer):
        invalid_group = AttackSeedGroup(
            seeds=[
                SeedObjective(value="invalid objective"),
                SeedPrompt(value="text only", data_type="text", role="user", sequence=0),
            ]
        )
        config = DatasetAttackConfiguration(
            seed_groups=[_figstep_group(), invalid_group],
            max_dataset_size=1,
        )
        scenario = FigStep(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match="text and image_path"):
            await _initialize(
                scenario=scenario,
                target=mock_objective_target,
                dataset_config=config,
            )


@pytest.mark.usefixtures("patch_central_database")
class TestFigStepAtomicAttacks:
    async def test_default_builds_text_baseline_then_visual_attack(self, mock_objective_target, mock_objective_scorer):
        groups = [_figstep_group(objective="objective one"), _figstep_group(objective="objective two")]
        scenario = FigStep(objective_scorer=mock_objective_scorer)

        await _initialize(
            scenario=scenario,
            target=mock_objective_target,
            dataset_config=_inline_config(groups=groups),
        )

        assert [attack.atomic_attack_name for attack in scenario._atomic_attacks] == [
            "baseline",
            "visual_jailbreak_inline",
        ]
        assert isinstance(scenario._atomic_attacks[1].attack_technique.attack, PromptSendingAttack)
        assert scenario._atomic_attacks[1].seed_groups == groups

    async def test_baseline_preserves_objectives_but_removes_visual_prompts(
        self, mock_objective_target, mock_objective_scorer
    ):
        groups = [_figstep_group(objective="objective one"), _figstep_group(objective="objective two")]
        scenario = FigStep(objective_scorer=mock_objective_scorer)

        await _initialize(
            scenario=scenario,
            target=mock_objective_target,
            dataset_config=_inline_config(groups=groups),
        )

        baseline, visual = scenario._atomic_attacks
        assert baseline.objectives == visual.objectives == ["objective one", "objective two"]
        assert all(group.next_message is None for group in baseline.seed_groups)
        assert all(group.next_message is not None for group in visual.seed_groups)

    async def test_baseline_can_be_disabled(self, mock_objective_target, mock_objective_scorer):
        scenario = FigStep(objective_scorer=mock_objective_scorer)

        await _initialize(
            scenario=scenario,
            target=mock_objective_target,
            dataset_config=_inline_config(),
            include_baseline=False,
        )

        assert [attack.atomic_attack_name for attack in scenario._atomic_attacks] == ["visual_jailbreak_inline"]

    async def test_memory_labels_are_forwarded(self, mock_objective_target, mock_objective_scorer):
        scenario = FigStep(objective_scorer=mock_objective_scorer)
        labels = {"operation": "figstep-test"}

        await _initialize(
            scenario=scenario,
            target=mock_objective_target,
            dataset_config=_inline_config(),
            memory_labels=labels,
        )

        assert all(attack._memory_labels == labels for attack in scenario._atomic_attacks)

    async def test_user_converters_are_applied_only_to_visual_attack(
        self, mock_objective_target, mock_objective_scorer
    ):
        converter = Base64Converter()
        scenario = FigStep(objective_scorer=mock_objective_scorer)

        await _initialize(
            scenario=scenario,
            target=mock_objective_target,
            dataset_config=_inline_config(),
            technique_converters={FigStepTechnique.VisualJailbreak.value: [converter]},
        )

        baseline, visual = scenario._atomic_attacks
        baseline_attack = baseline.attack_technique.attack
        visual_attack = visual.attack_technique.attack
        assert isinstance(baseline_attack, PromptSendingAttack)
        assert isinstance(visual_attack, PromptSendingAttack)
        assert baseline_attack._request_converters == []
        assert len(visual_attack._request_converters) == 1
        assert visual_attack._request_converters[0].converters == [converter]


class TestFigStepTechnique:
    def test_all_expands_to_visual_jailbreak(self):
        expanded = FigStepTechnique.expand({FigStepTechnique.ALL})

        assert expanded == [FigStepTechnique.VisualJailbreak]
