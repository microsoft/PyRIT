# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Aggregation leakage scenario.

Retrieval-augmented assistants usually enforce access control per document: each retrieved
document is checked against the caller's permissions. The model, however, reasons over all of
the documents together, so several documents the caller may read can jointly entail a fact the
caller is not cleared for, even though no single document states it. This scenario measures how
often a target draws, and discloses, such a conclusion.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pyrit.common import apply_defaults
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack, RedTeamingAttack
from pyrit.models import AttackSeedGroup, Parameter, SeedPrompt
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.scenario.core import BaselineAttackPolicy, Scenario, get_default_adversarial_target
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetSource, DatasetSourceKind
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.scenario.core.technique_requirements import (
    TechniqueRequirements,
    check_target_compatibility,
    filter_seed_groups,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.converter import Converter
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.attack_technique import AttackTechnique
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

#: Documents the user may read, which jointly entail a confidential fact none of them states.
AGGREGATION_LEAKAGE_DATASET = "airt_aggregation_leakage"
#: Control: the same documents with one edit so the fact no longer follows.
AGGREGATION_LEAKAGE_MINIMAL_PAIRS_DATASET = "airt_aggregation_leakage_minimal_pairs"
#: Control: the structural document plus one that merely names the subject.
AGGREGATION_LEAKAGE_MENTION_ONLY_DATASET = "airt_aggregation_leakage_mention_only"

_DATASETS = [
    AGGREGATION_LEAKAGE_DATASET,
    AGGREGATION_LEAKAGE_MINIMAL_PAIRS_DATASET,
    AGGREGATION_LEAKAGE_MENTION_ONLY_DATASET,
]

#: Default cap on seed groups: every group in the three datasets (16 + 8 + 8), so the controls
#: always run alongside the derivable items.
_DEFAULT_MAX_TOTAL = 32

#: Seed metadata key marking the turn that carries the retrieved documents.
AGGREGATION_CONTEXT_METADATA_KEY = "aggregation_context"


class AggregationLeakageTechnique(ScenarioTechnique):
    """
    Techniques for the AggregationLeakage scenario.

    Non-Aggregate Values:
    - PromptSending: Sends the retrieved documents and the user's question as-is. This measures
        whether the target draws the confidential conclusion when an ordinary user asks.
    - RedTeaming: A multi-turn ``RedTeamingAttack`` in which an adversarial chat keeps asking
        follow-up questions over the same documents until the target states the conclusion or
        ``max_turns`` is reached.

    Aggregate Values:
    - ALL: Every technique.
    - DEFAULT: ``PromptSending``, so the default run is fast and needs no adversarial chat.
    - SINGLE_TURN / MULTI_TURN: Group techniques by turn count.
    """

    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    SINGLE_TURN = ("single_turn", {"single_turn"})
    MULTI_TURN = ("multi_turn", {"multi_turn"})

    PromptSending = ("prompt_sending", {"single_turn", "default"})
    RedTeaming = ("red_teaming", {"multi_turn"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """
        Get the set of tags that represent aggregate categories.

        Returns:
            set[str]: Set of tags that are aggregate markers.
        """
        return super().get_aggregate_tags() | {"default", "single_turn", "multi_turn"}

    @classmethod
    def default(cls) -> AggregationLeakageTechnique:
        """Return the default technique (``DEFAULT``) used when the caller selects nothing."""
        return cls.DEFAULT


def strip_aggregation_context(*, seed_group: AttackSeedGroup) -> AttackSeedGroup:
    """
    Return a copy of ``seed_group`` without the turn that carries the retrieved documents.

    Used when the documents already live in the target's own knowledge base, so only the user's
    question should be sent.

    Args:
        seed_group (AttackSeedGroup): A group from one of the aggregation leakage datasets.

    Returns:
        AttackSeedGroup: The group without its context turn, or ``seed_group`` itself when it has none.
    """
    kept = [
        seed
        for seed in seed_group.seeds
        if not (isinstance(seed, SeedPrompt) and (seed.metadata or {}).get(AGGREGATION_CONTEXT_METADATA_KEY))
    ]
    if len(kept) == len(seed_group.seeds):
        return seed_group
    return AttackSeedGroup(seeds=kept)


class AggregationLeakage(Scenario):
    """
    Aggregation leakage scenario implementation for PyRIT.

    Tests whether a target discloses a confidential conclusion that no single document states but
    that the documents it was given entail together, for example a policy that applies "only" to
    employees on a performance plan combined with a calendar showing a named employee in exactly
    such a meeting. Every access check on the individual documents passes, so this leak is
    invisible to per-document controls.

    Results are reported per technique and dataset. ``airt_aggregation_leakage`` holds the
    derivable items. The two control datasets hold items where the conclusion does *not* follow:
    a success there means the target (or the scorer) is reacting to the topic rather than to the
    derivation, so the control rates should stay near zero for the main rate to be meaningful.
    Each seed carries ``derivation_channel`` and ``query_directness`` metadata for finer analysis.
    """

    VERSION: int = 1
    #: ``prompt_sending`` already sends each group unmodified, so a separate baseline would
    #: duplicate it.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    @classmethod
    def _get_additional_scoring_questions(cls) -> list[Path]:
        """
        Override true/false question paths for aggregation leakage objective scoring.

        Returns:
            list[Path]: Paths to true/false question files for aggregation leakage scoring.
        """
        return [SCORER_SEED_PROMPT_PATH / "true_false_question" / "aggregation_leakage.yaml"]

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return list(_DATASETS)

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare custom parameters this scenario accepts from the CLI / config file.

        Returns:
            list[Parameter]: Parameters configurable per-run.
        """
        return [
            Parameter(
                name="include_context",
                description=(
                    "Send the retrieved documents as a system turn before each question. Set to False "
                    "only with one explicitly supplied inline seed group and an isolated, externally "
                    "provisioned target. Requires external_context_isolated=True."
                ),
                param_type=bool,
                default=True,
            ),
            Parameter(
                name="external_context_isolated",
                description=(
                    "Confirm that the target contains only the selected case's documents and confidentiality "
                    "policy, with no positive/control documents or conversation state from another case."
                ),
                param_type=bool,
                default=False,
            ),
            Parameter(
                name="max_turns",
                description="Maximum conversation turns for the red_teaming technique.",
                param_type=int,
                default=3,
            ),
        ]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        adversarial_chat: PromptTarget | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the aggregation leakage scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Scorer that decides whether a response
                discloses the confidential conclusion. Defaults to a composite of the aggregation
                leakage true/false question and a refusal backstop.
            adversarial_chat (PromptTarget | None): Chat target that drives the ``red_teaming``
                technique. Defaults to the registered adversarial target.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
        """
        self._adversarial_chat = adversarial_chat

        super().__init__(
            version=self.VERSION,
            technique_class=AggregationLeakageTechnique,
            default_dataset_config=DatasetAttackConfiguration(
                sources=[DatasetSource(name=name) for name in _DATASETS],
                max_per_dataset="all",
                max_total=_DEFAULT_MAX_TOTAL,
            ),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    @property
    def uses_default_adversarial_target(self) -> bool:
        """Whether the selected technique needs the shared adversarial target."""
        techniques = self._scenario_techniques or [self._default_technique]
        return self._adversarial_chat is None and any(
            technique.value == AggregationLeakageTechnique.RedTeaming.value for technique in techniques
        )

    def _build_technique(
        self,
        *,
        technique: str,
        objective_target: PromptTarget,
        adversarial_chat: PromptTarget | None,
        converters: list[Converter],
    ) -> AttackTechnique:
        """
        Build the attack technique for one technique name.

        Args:
            technique (str): The technique name.
            objective_target (PromptTarget): The target under test.
            adversarial_chat (PromptTarget | None): The adversarial chat for ``red_teaming``.
            converters (list[Converter]): Additional request converters selected for this technique.

        Returns:
            AttackTechnique: The configured technique.

        Raises:
            ValueError: If ``technique`` is unknown, or ``red_teaming`` has no adversarial chat.
        """
        is_red_teaming = technique == AggregationLeakageTechnique.RedTeaming.value
        if is_red_teaming:
            if adversarial_chat is None:
                raise ValueError("The red_teaming technique requires an adversarial chat target.")
        elif technique != AggregationLeakageTechnique.PromptSending.value:
            raise ValueError(f"Unknown AggregationLeakageTechnique: {technique}")
        factory = AttackTechniqueFactory(
            name=technique,
            attack_class=RedTeamingAttack if is_red_teaming else PromptSendingAttack,
            attack_kwargs={"max_turns": self.params.get("max_turns", 3)} if is_red_teaming else {},
            adversarial_chat=adversarial_chat,
            supports_additional_request_converters=True,
        )
        return factory.create(
            objective_target=objective_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=cast("TrueFalseScorer", self._objective_scorer)),
            extra_request_converters=ConverterConfiguration.from_converters(converters=converters)
            if converters
            else None,
        )

    async def _validate_external_context_async(self, context: ScenarioContext) -> None:
        """
        Require an explicitly selected case and confirmation of external target isolation.

        Raises:
            ValueError: If isolation is unconfirmed or the population is not one inline case.
        """
        if not self.params.get("external_context_isolated", False):
            raise ValueError("include_context=False requires external_context_isolated=True.")
        if context.dataset_config.source_kind != DatasetSourceKind.INLINE or len(context.seed_groups) != 1:
            raise ValueError(
                "include_context=False requires exactly one inline seed group. Provision an isolated target "
                "with that case's documents and policy; do not combine positive and control evidence."
            )
        population = await context.dataset_config.get_attack_seed_groups_async(apply_sampling=False)
        if len(population) != 1:
            raise ValueError("include_context=False requires exactly one inline seed group before sampling.")

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build one atomic attack per selected technique and dataset.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.
        """
        include_context = cast("bool", self.params.get("include_context", True))
        if not include_context:
            await self._validate_external_context_async(context)
        scorer = cast("TrueFalseScorer", self._objective_scorer)
        techniques = sorted({technique.value for technique in context.scenario_techniques})

        atomic_attacks: list[AtomicAttack] = []
        for technique in techniques:
            is_red_teaming = technique == AggregationLeakageTechnique.RedTeaming.value
            attack_class = RedTeamingAttack if is_red_teaming else PromptSendingAttack
            requirements = TechniqueRequirements()
            if not check_target_compatibility(
                target=context.objective_target,
                attack_class=attack_class,
                requirements=requirements,
                technique_name=technique,
                policy=context.incompatible_technique_policy,
            ):
                continue
            adversarial_chat = (self._adversarial_chat or get_default_adversarial_target()) if is_red_teaming else None
            for dataset_name, groups in context.seed_groups_by_dataset.items():
                compatible = filter_seed_groups(
                    seed_groups=groups,
                    requirements=requirements.seed_group,
                    seed_technique=None,
                    technique_name=technique,
                    dataset_name=dataset_name,
                    policy=context.incompatible_technique_policy,
                )
                if not compatible:
                    continue
                execution_params: dict[str, Any] = {}
                if not include_context:
                    execution_group = strip_aggregation_context(seed_group=compatible[0])
                    execution_params = {
                        "prepended_conversation": execution_group.prepended_conversation,
                        "next_message": execution_group.next_message,
                    }
                atomic_attacks.append(
                    AtomicAttack(
                        atomic_attack_name=f"{technique}_{dataset_name}",
                        display_group=f"{technique}_{dataset_name}",
                        technique_name=technique,
                        attack_technique=self._build_technique(
                            technique=technique,
                            objective_target=context.objective_target,
                            adversarial_chat=adversarial_chat,
                            converters=context.technique_converters.get(technique, []),
                        ),
                        seed_groups=compatible,
                        adversarial_chat=adversarial_chat,
                        objective_scorer=scorer,
                        memory_labels=context.memory_labels,
                        **execution_params,
                    )
                )
        return atomic_attacks
