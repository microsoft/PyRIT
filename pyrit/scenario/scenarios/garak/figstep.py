# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""FigStep visual-jailbreak scenario."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from pyrit.common import apply_defaults
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import AttackSeedGroup, PromptDataType
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import CapabilityName, TargetRequirements
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import (
    INLINE_DATASET_NAME,
    DatasetAttackConfiguration,
    DatasetConfiguration,
    DatasetSourceKind,
)
from pyrit.scenario.core.matrix_atomic_attack_builder import build_baseline_atomic_attack
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_technique import ScenarioTechnique

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import TrueFalseScorer


DEFAULT_MAX_DATASET_SIZE = 10
_FIGSTEP_DATASET_NAMES: frozenset[str] = frozenset({"figstep", "figstep_pro"})
_FIGSTEP_INPUT_MODALITIES = cast(
    "frozenset[frozenset[PromptDataType]]",
    frozenset({frozenset({"text", "image_path"})}),
)


class FigStepTechnique(ScenarioTechnique):
    """Techniques for the FigStep scenario."""

    ALL = ("all", {"all"})
    VisualJailbreak = ("visual_jailbreak", set[str]())


_VISUAL_JAILBREAK_FACTORY = AttackTechniqueFactory(
    name=FigStepTechnique.VisualJailbreak.value,
    attack_class=PromptSendingAttack,
    technique_tags=["single_turn"],
)


class FigStep(Scenario):
    """
    Run FigStep or FigStep-Pro typographic-image jailbreaks against a multimodal target.

    The selected FigStep dataset supplies one harmful objective and a grouped text-plus-image
    request for each SafeBench item. ``PromptSendingAttack`` sends that grouped request and scores
    whether the response substantively fulfills the harmful objective. The default baseline sends
    the same sampled objectives directly as text.

    Reference: [@gong2025figstep]
    """

    VERSION: int = 1
    TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements(
        native_required=frozenset({CapabilityName.MULTI_MESSAGE_PIECES}),
        required_input_modalities=_FIGSTEP_INPUT_MODALITIES,
    )

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return the built-in datasets supported by this scenario."""
        return sorted(_FIGSTEP_DATASET_NAMES)

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
        Initialize the FigStep scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Scorer for harmful objective fulfillment.
            scenario_result_id (str | None): Optional scenario result ID to resume.
        """
        objective_scorer = objective_scorer or self._get_default_objective_scorer()
        self._scorer_config = AttackScoringConfig(objective_scorer=objective_scorer)

        super().__init__(
            version=self.VERSION,
            technique_class=FigStepTechnique,
            default_dataset_config=DatasetAttackConfiguration(
                dataset_names=["figstep"],
                max_dataset_size=DEFAULT_MAX_DATASET_SIZE,
            ),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    @staticmethod
    def _get_dataset_label(*, dataset_config: DatasetConfiguration) -> str:
        """
        Validate the dataset source and return its stable attack label.

        Inline groups are supported for programmatic use. Named configurations must select exactly
        one built-in FigStep dataset because other datasets do not define the required visual
        jailbreak payload.

        Returns:
            str: ``figstep``, ``figstep_pro``, or ``inline``.

        Raises:
            ValueError: If a named configuration does not select exactly one FigStep dataset.
        """
        if dataset_config.source_kind is DatasetSourceKind.INLINE:
            return INLINE_DATASET_NAME

        dataset_names = dataset_config.dataset_names
        if len(dataset_names) != 1 or dataset_names[0] not in _FIGSTEP_DATASET_NAMES:
            raise ValueError(
                f"FigStep requires exactly one named dataset: 'figstep' or 'figstep_pro'. Received {dataset_names}."
            )
        return dataset_names[0]

    @staticmethod
    def _validate_seed_groups(*, seed_groups: list[AttackSeedGroup]) -> None:
        """
        Validate that every group is a single-turn text-plus-image request.

        Raises:
            ValueError: If no groups exist or a group lacks text or image input.
        """
        if not seed_groups:
            raise ValueError("FigStep requires at least one multimodal seed group to attack.")

        for seed_group in seed_groups:
            if seed_group.prepended_conversation:
                raise ValueError("FigStep seed groups must contain exactly one user message.")
            message = seed_group.next_message
            if message is None:
                raise ValueError("FigStep seed groups must include a next message.")
            data_types = {piece.original_value_data_type for piece in message.message_pieces}
            if not {"text", "image_path"} <= data_types:
                raise ValueError("FigStep seed groups must include text and image_path pieces in one next message.")

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        """
        Resolve one approved FigStep dataset or validated inline groups.

        Returns:
            dict[str, list[AttackSeedGroup]]: Valid FigStep groups keyed by dataset.
        """
        self._get_dataset_label(dataset_config=self._dataset_config)

        validate_before_sampling = apply_sampling and self._dataset_config.max_dataset_size is not None
        validation_sampling = apply_sampling and not validate_before_sampling
        groups_by_dataset = await super()._resolve_seed_groups_by_dataset_async(apply_sampling=validation_sampling)
        self._validate_seed_groups(seed_groups=[group for groups in groups_by_dataset.values() for group in groups])

        if validate_before_sampling:
            return await super()._resolve_seed_groups_by_dataset_async(apply_sampling=True)
        return groups_by_dataset

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the direct-text baseline and selected visual-jailbreak attack.

        Returns:
            list[AtomicAttack]: The ordered baseline and visual attacks.
        """
        seed_groups = list(context.seed_groups)
        self._validate_seed_groups(seed_groups=seed_groups)
        dataset_label = self._get_dataset_label(dataset_config=context.dataset_config)

        atomic_attacks: list[AtomicAttack] = []
        if context.include_baseline:
            baseline_seed_groups = [AttackSeedGroup(seeds=[seed_group.objective]) for seed_group in seed_groups]
            atomic_attacks.append(
                build_baseline_atomic_attack(
                    objective_target=context.objective_target,
                    objective_scorer=self._objective_scorer,
                    seed_groups=baseline_seed_groups,
                    memory_labels=context.memory_labels,
                )
            )

        user_converters = ConverterConfiguration.from_converters(
            converters=self._technique_converters.get(FigStepTechnique.VisualJailbreak.value, [])
        )
        attack_technique = _VISUAL_JAILBREAK_FACTORY.create(
            objective_target=context.objective_target,
            attack_scoring_config=self._scorer_config,
            extra_request_converters=user_converters,
        )
        atomic_attacks.append(
            AtomicAttack(
                atomic_attack_name=f"{FigStepTechnique.VisualJailbreak.value}_{dataset_label}",
                display_group=dataset_label,
                attack_technique=attack_technique,
                seed_groups=seed_groups,
                memory_labels=context.memory_labels,
            )
        )
        return atomic_attacks
