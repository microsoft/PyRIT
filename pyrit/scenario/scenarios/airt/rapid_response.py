# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
RapidResponse scenario — technique-based rapid content-harms testing.

Strategies select **attack techniques** (PromptSending, RolePlay,
ManyShot, TAP). Datasets select **harm categories** (hate, fairness,
violence, …). Use ``--dataset-names`` to narrow which harm categories
to test.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pyrit.common import apply_defaults
from pyrit.executor.attack import AttackScoringConfig
from pyrit.prompt_target import PromptChatTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_strategy import (
    ScenarioCompositeStrategy,
    ScenarioStrategy,
)
from pyrit.score import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


class RapidResponseStrategy(ScenarioStrategy):
    """
    Attack-technique strategies for the RapidResponse scenario.

    Each non-aggregate member maps to a single attack technique.
    Aggregates (ALL, DEFAULT, SINGLE_TURN, MULTI_TURN) expand to
    all techniques that share the corresponding tag.

    ``ScenarioStrategy`` members should map 1:1 to selectable attack
    techniques or aggregates of techniques. They are the user-facing
    selection API; ``AttackTechniqueFactory`` is the execution
    abstraction.
    """

    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    SINGLE_TURN = ("single_turn", {"single_turn"})
    MULTI_TURN = ("multi_turn", {"multi_turn"})

    PromptSending = ("prompt_sending", {"single_turn", "default"})
    RolePlay = ("role_play", {"single_turn"})
    ManyShot = ("many_shot", {"multi_turn", "default"})
    TAP = ("tap", {"multi_turn"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all", "default", "single_turn", "multi_turn"}


class RapidResponse(Scenario):
    """
    Rapid Response scenario for content-harms testing.

    Tests model behaviour across harm categories using selectable attack
    techniques. Strategies control *how* prompts are delivered (e.g.
    prompt_sending, role_play, many_shot, TAP). Datasets control *what*
    harm content is tested (e.g. hate, violence, sexual). Use
    ``--dataset-names`` to filter harm categories.
    """

    VERSION: int = 2

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        return RapidResponseStrategy

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        return RapidResponseStrategy.DEFAULT

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        return DatasetConfiguration(
            dataset_names=[
                "airt_hate",
                "airt_fairness",
                "airt_violence",
                "airt_sexual",
                "airt_harassment",
                "airt_misinformation",
                "airt_leakage",
            ],
            max_dataset_size=4,
        )

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_chat: PromptChatTarget | None = None,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the Rapid Response scenario.

        Args:
            adversarial_chat: Chat target for multi-turn / adversarial
                attacks (RolePlay, TAP). When provided, overrides the
                default adversarial target baked into technique factories.
            objective_scorer: Scorer for evaluating attack success.
                Defaults to a composite Azure-Content-Filter + refusal
                scorer.
            scenario_result_id: Optional ID of an existing scenario
                result to resume.
        """
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )
        self._adversarial_chat = adversarial_chat

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=RapidResponseStrategy,
            scenario_result_id=scenario_result_id,
        )

    def _build_atomic_attack_name(self, *, technique_name: str, seed_group_name: str) -> str:
        """Group results by harm category (dataset) rather than technique."""
        return seed_group_name

    def get_attack_technique_factories(self) -> dict[str, "AttackTechniqueFactory"]:
        """
        Register core techniques with this scenario's adversarial chat target.
        """
        from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
        from pyrit.scenario.core.scenario_techniques import ScenarioTechniqueRegistrar

        ScenarioTechniqueRegistrar(adversarial_chat=self._adversarial_chat).register()
        return AttackTechniqueRegistry.get_registry_singleton().get_factories()

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build atomic attacks from selected techniques × harm datasets.

        Iterates over every (technique, harm-dataset) pair and creates
        an ``AtomicAttack`` for each.  The ``_build_atomic_attack_name``
        override groups results by harm category.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        selected_techniques = ScenarioCompositeStrategy.extract_single_strategy_values(
            self._scenario_composites, strategy_type=RapidResponseStrategy
        )

        factories = self.get_attack_technique_factories()
        seed_groups_by_dataset = self._dataset_config.get_seed_attack_groups()

        scoring_config = AttackScoringConfig(objective_scorer=self._objective_scorer)

        # Resolve adversarial_chat for AtomicAttack parameter building.
        from pyrit.scenario.core.scenario_techniques import get_default_adversarial_target

        adversarial_chat = self._adversarial_chat or get_default_adversarial_target()

        atomic_attacks: list[AtomicAttack] = []
        for technique_name in selected_techniques:
            factory = factories.get(technique_name)
            if factory is None:
                logger.warning(f"No factory for technique '{technique_name}', skipping.")
                continue

            # TAP creates its own FloatScaleThresholdScorer internally when no
            # scoring config is provided.  Passing the scenario's TrueFalseScorer
            # would fail TAP's type validation.
            scoring_for_technique = None if technique_name == "tap" else scoring_config

            attack_technique = factory.create(
                objective_target=self._objective_target,
                attack_scoring_config_override=scoring_for_technique,
            )

            for dataset_name, seed_groups in seed_groups_by_dataset.items():
                atomic_attacks.append(
                    AtomicAttack(
                        atomic_attack_name=self._build_atomic_attack_name(
                            technique_name=technique_name,
                            seed_group_name=dataset_name,
                        ),
                        attack_technique=attack_technique,
                        seed_groups=list(seed_groups),
                        adversarial_chat=adversarial_chat,
                        objective_scorer=self._objective_scorer,
                        memory_labels=self._memory_labels,
                    )
                )

        return atomic_attacks
