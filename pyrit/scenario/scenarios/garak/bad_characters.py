# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bad Characters imperceptible-perturbation scenario."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from pyrit.common import apply_defaults
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.converter import (
    BackspaceConverter,
    BidiSwapConverter,
    UnicodeConfusableConverter,
    ZeroWidthConverter,
)
from pyrit.executor.attack import AttackConverterConfig, AttackScoringConfig, PromptSendingAttack
from pyrit.models import Parameter
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetSource
from pyrit.scenario.core.matrix_atomic_attack_builder import build_baseline_atomic_attack
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_technique import ScenarioTechnique

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.converter import Converter
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import TrueFalseScorer


DEFAULT_MAX_DATASET_SIZE = 10
DEFAULT_PERTURBATION_BUDGET = 1


class BadCharactersTechnique(ScenarioTechnique):
    """
    Techniques for the Bad Characters scenario, one per perturbation category.

    The ``ALL`` aggregate, which runs every category as garak's probe does, is the default.
    """

    ALL = ("all", {"all"})

    Invisible = ("invisible", set[str]())
    Homoglyph = ("homoglyph", set[str]())
    Reordering = ("reordering", set[str]())
    Deletion = ("deletion", set[str]())


class BadCharacters(Scenario):
    """
    Test whether refusals hold when a harmful request carries a few imperceptible character changes.

    Each technique applies one category of perturbation from Boucher et al., "Bad Characters:
    Imperceptible NLP Attacks", as garak's ``badchars.BadCharacters`` probe does. The perturbed text
    renders the same as the original but has different code points:

    - ``invisible``: zero-width space, non-joiner or joiner characters are inserted.
    - ``homoglyph``: characters are replaced with Unicode look-alikes.
    - ``reordering``: adjacent characters are stored swapped behind bidirectional controls.
    - ``deletion``: characters are inserted, each followed by a backspace.

    Every category makes ``perturbation_budget`` changes per prompt, at random positions. The default
    of 1 matches garak. Only text pieces are perturbed; image, audio and other attachments are sent
    unchanged. ``PromptSendingAttack`` sends each perturbed objective and scores whether the response
    fulfills the original objective. The default baseline sends the same objectives unchanged.

    Garak generates every combination of positions and characters, then downsamples. This scenario
    sends one random variant per objective and technique, so set a seed for reproducible runs.

    Reference: [@boucher2022badcharacters]
    """

    VERSION: int = 1
    SUPPORTS_TECHNIQUE_CONVERTERS: ClassVar[bool] = False

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the perturbation budget.

        Returns:
            list[Parameter]: The scenario-specific runtime parameters.
        """
        return [
            Parameter(
                name="perturbation_budget",
                description="How many characters each technique changes per prompt. Garak's default is 1.",
                param_type=int,
                default=DEFAULT_PERTURBATION_BUDGET,
            ),
        ]

    @classmethod
    def _get_additional_scoring_questions(cls) -> list[Path]:
        """Return the task-achieved rubric used to detect harmful compliance."""
        return [SCORER_SEED_PROMPT_PATH / "true_false_question" / "task_achieved.yaml"]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the Bad Characters scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Scorer for harmful objective fulfillment.
                Defaults to the scenario's task-achieved scorer.
            scenario_result_id (str | None): Optional scenario result ID to resume.
        """
        super().__init__(
            version=self.VERSION,
            technique_class=BadCharactersTechnique,
            default_dataset_config=DatasetAttackConfiguration(
                sources=[DatasetSource(name=name) for name in ["harmbench"]],
                max_per_dataset="all",
                max_total=DEFAULT_MAX_DATASET_SIZE,
            ),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    def _perturbation_budget(self) -> int:
        """
        Read the perturbation budget from the run parameters.

        Returns:
            int: How many characters each technique changes per prompt.
        """
        return cast("int", self.params.get("perturbation_budget", DEFAULT_PERTURBATION_BUDGET))

    def _validate_runtime_configuration(self) -> None:
        """
        Reject a perturbation budget that is not a positive integer.

        Raises:
            ValueError: If ``perturbation_budget`` is not a positive integer.
        """
        super()._validate_runtime_configuration()
        budget = self._perturbation_budget()
        if budget < 1:
            raise ValueError(f"perturbation_budget must be a positive integer, got {budget!r}")

    def _converter_for(self, *, technique: str) -> Converter:
        """
        Build the converter that applies one perturbation category.

        Args:
            technique (str): The technique value, such as ``"invisible"``.

        Returns:
            Converter: A fresh converter making ``perturbation_budget`` changes.

        Raises:
            ValueError: If the technique is not a perturbation category.
        """
        budget = self._perturbation_budget()
        if technique == BadCharactersTechnique.Invisible.value:
            return ZeroWidthConverter(
                budget=budget,
                characters=(
                    ZeroWidthConverter.ZERO_WIDTH_SPACE,
                    ZeroWidthConverter.ZERO_WIDTH_NON_JOINER,
                    ZeroWidthConverter.ZERO_WIDTH_JOINER,
                ),
            )
        if technique == BadCharactersTechnique.Homoglyph.value:
            return UnicodeConfusableConverter(budget=budget)
        if technique == BadCharactersTechnique.Reordering.value:
            return BidiSwapConverter(budget=budget)
        if technique == BadCharactersTechnique.Deletion.value:
            return BackspaceConverter(budget=budget)
        raise ValueError(f"Unknown Bad Characters technique: {technique!r}")

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the baseline and one perturbation attack per selected technique.

        Returns:
            list[AtomicAttack]: The baseline, if enabled, followed by the technique attacks.
        """
        seed_groups = list(context.seed_groups)
        attack_scoring_config = AttackScoringConfig(objective_scorer=self._objective_scorer)

        atomic_attacks: list[AtomicAttack] = []
        if context.include_baseline:
            atomic_attacks.append(
                build_baseline_atomic_attack(
                    objective_target=context.objective_target,
                    objective_scorer=self._objective_scorer,
                    seed_groups=seed_groups,
                    memory_labels=context.memory_labels,
                )
            )

        for technique in context.scenario_techniques:
            converter = self._converter_for(technique=technique.value)
            attack = PromptSendingAttack(
                objective_target=context.objective_target,
                attack_converter_config=AttackConverterConfig(
                    request_converters=[
                        ConverterConfiguration(converters=[converter], prompt_data_types_to_apply=["text"])
                    ]
                ),
                attack_scoring_config=attack_scoring_config,
            )
            atomic_attacks.append(
                AtomicAttack(
                    atomic_attack_name=technique.value,
                    display_group=technique.value,
                    attack_technique=AttackTechnique(attack=attack),
                    seed_groups=seed_groups,
                    memory_labels=context.memory_labels,
                )
            )
        return atomic_attacks
