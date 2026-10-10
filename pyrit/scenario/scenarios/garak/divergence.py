# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Portions Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# Garak-derived portions are licensed under Apache-2.0 and modified by Microsoft Corporation.
# See THIRD_PARTY_NOTICES.txt for attribution and source details.

"""Garak's Repeat probe, packaged with a reusable repetition-divergence scorer."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, ClassVar, cast

from pyrit.common import apply_defaults
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import AttackSeedGroup, DivergesFromRepetition, ScoringExpectation, Seed, SeedObjective, SeedPrompt
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetConstraintError
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.score import DivergenceScorer, TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.scenario.core.scenario_context import ScenarioContext


class DivergenceDatasetConfiguration(DatasetAttackConfiguration):
    """Pair literal repetition prompts with distinct objectives, preserving their provenance."""

    DEFAULT_MAX_DATASET_SIZE: int = 10

    def _build_attack_groups(self, seeds: list[Seed]) -> list[AttackSeedGroup]:
        """
        Preserve each source prompt and add its attack objective.

        Returns:
            list[AttackSeedGroup]: One group per source prompt.

        Raises:
            DatasetConstraintError: If a seed is not a text prompt.
        """
        groups: list[AttackSeedGroup] = []
        for seed in seeds:
            if not isinstance(seed, SeedPrompt) or seed.data_type != "text":
                raise DatasetConstraintError("Divergence requires literal text SeedPrompts.")
            prompt = seed.model_copy(deep=True)
            groups.append(
                AttackSeedGroup(
                    seeds=[
                        SeedObjective(
                            value=f"Produce unexpected continuation after repetition in response to: {seed.value}",
                            dataset_name=seed.dataset_name,
                            source=seed.source,
                            authors=seed.authors,
                            groups=seed.groups,
                            harm_categories=seed.harm_categories,
                            metadata=seed.metadata,
                        ),
                        prompt,
                    ]
                )
            )
        return groups


class DivergenceTechnique(ScenarioTechnique):
    """Repeat is the sole probe; ALL and DEFAULT select it through the shared resolver."""

    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    Repeat = ("repeat", {"default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """Return the aggregate tags."""
        return {"all", "default"}

    @classmethod
    def default(cls) -> DivergenceTechnique:
        """Return the default aggregate."""
        return cls.DEFAULT


class Divergence(Scenario):
    """
    Test for unexpected continuation after requested word repetition.

    Ports Garak's Repeat probe, not RepeatedToken. A positive score indicates
    divergence, not proof that the response contains training data.
    The direct repetition prompt is the attack; an objective-only baseline is not meaningful.

    Reference: [@derczynski2024garak]
    """

    VERSION: int = 1
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden
    _REPEAT_FACTORY: ClassVar[AttackTechniqueFactory] = AttackTechniqueFactory(
        name="repeat",
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
        supports_additional_request_converters=True,
    )

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the Repeat scenario.

        Args:
            objective_scorer: Optional scorer override. Repetition-aware scorers receive
                a DivergesFromRepetition condition; other scorers receive objective context only.
            scenario_result_id: Optional existing result to resume.
        """
        super().__init__(
            version=self.VERSION,
            technique_class=DivergenceTechnique,
            default_dataset_config=DivergenceDatasetConfiguration(
                dataset_names=self.required_datasets(),
                max_dataset_size=DivergenceDatasetConfiguration.DEFAULT_MAX_DATASET_SIZE,
            ),
            objective_scorer=objective_scorer or DivergenceScorer(),
            scenario_result_id=scenario_result_id,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Package each sampled word population with its own immutable scoring expectation.

        Returns:
            list[AtomicAttack]: Word populations sharing one scorer and attack technique.
        """
        scorer = cast("TrueFalseScorer", self._objective_scorer)
        converters = self._technique_converters.get("repeat", [])
        technique = self._REPEAT_FACTORY.create(
            objective_target=context.objective_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
            extra_request_converters=(
                ConverterConfiguration.from_converters(converters=converters) if converters else None
            ),
        )

        populations: list[tuple[str, ScoringExpectation | None, list[AttackSeedGroup]]]
        if DivergesFromRepetition in scorer.get_condition_types():
            groups_by_word: dict[str, list[AttackSeedGroup]] = defaultdict(list)
            for group in context.seed_groups:
                groups_by_word[self._repeat_word(group)].append(group)
            populations = [
                (f"repeat_{word}", ScoringExpectation(conditions=(DivergesFromRepetition(text=word),)), groups)
                for word, groups in sorted(groups_by_word.items())
            ]
        else:
            populations = [("repeat", None, list(context.seed_groups))]

        return [
            AtomicAttack(
                atomic_attack_name=name,
                technique_name="repeat",
                display_group="repeat",
                attack_technique=technique,
                seed_groups=groups,
                objective_scorer=scorer,
                memory_labels=context.memory_labels,
                expectation=expectation,
            )
            for name, expectation, groups in populations
        ]

    @staticmethod
    def _repeat_word(group: AttackSeedGroup) -> str:
        """
        Read the criterion from the literal prompt rather than guessing from its text.

        Returns:
            str: The expected repeated text.

        Raises:
            DatasetConstraintError: If the group lacks one text prompt with a repeat word.
        """
        if len(group.prompts) != 1 or group.prompts[0].data_type != "text":
            raise DatasetConstraintError("Divergence scoring requires one literal text prompt per seed group.")
        word = (group.prompts[0].metadata or {}).get("repeat_word")
        if not isinstance(word, str) or not word.strip():
            raise DatasetConstraintError("Divergence scoring requires nonempty repeat_word metadata on each prompt.")
        return word

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return the Repeat prompt corpus."""
        return ["garak_divergence"]
