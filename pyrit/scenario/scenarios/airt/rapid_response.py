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
from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig
from pyrit.prompt_target import PromptChatTarget
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
from pyrit.score import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


def _build_rapid_response_strategy() -> type[ScenarioStrategy]:
    """
    Build the RapidResponse strategy class dynamically from SCENARIO_TECHNIQUES.

    Reads the spec list (pure data) — no registry interaction or target resolution.
    """
    from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
    from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES

    core_specs = [s for s in SCENARIO_TECHNIQUES if "core" in s.tags]

    return AttackTechniqueRegistry.build_strategy_class_from_specs(
        class_name="RapidResponseStrategy",
        specs=core_specs,
        aggregate_tags={
            "default": {"default"},
            "single_turn": {"single_turn"},
            "multi_turn": {"multi_turn"},
        },
    )


# Module-level symbol — populated lazily by get_strategy_class().
# Preserved for backward-compatible imports (e.g. content_harms.py alias).
RapidResponseStrategy: type[ScenarioStrategy] | None = None


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
        global RapidResponseStrategy
        if RapidResponseStrategy is None:
            RapidResponseStrategy = _build_rapid_response_strategy()
        return RapidResponseStrategy

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        strategy_class = cls.get_strategy_class()
        return strategy_class("default")

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
            strategy_class=self.get_strategy_class(),
            scenario_result_id=scenario_result_id,
        )

    def _build_display_group(self, *, technique_name: str, seed_group_name: str) -> str:
        """Group results by harm category (dataset) rather than technique."""
        return seed_group_name

    def get_attack_technique_factories(self) -> dict[str, "AttackTechniqueFactory"]:
        """
        Register core techniques and return factories from the registry.
        """
        from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
        from pyrit.scenario.core.scenario_techniques import register_scenario_techniques

        register_scenario_techniques()
        return AttackTechniqueRegistry.get_registry_singleton().get_factories()

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build atomic attacks from selected techniques × harm datasets.

        Iterates over every (technique, harm-dataset) pair and creates
        an ``AtomicAttack`` for each.  Each has a unique compound
        ``atomic_attack_name`` and a ``display_group`` for user-facing
        aggregation by harm category.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        selected_techniques = {s.value for s in self._scenario_strategies}

        factories = self.get_attack_technique_factories()
        seed_groups_by_dataset = self._dataset_config.get_seed_attack_groups()

        scoring_config = AttackScoringConfig(objective_scorer=self._objective_scorer)

        # Resolve adversarial_chat for AtomicAttack parameter building.
        from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
        from pyrit.scenario.core.scenario_techniques import get_default_adversarial_target

        registry = AttackTechniqueRegistry.get_registry_singleton()
        adversarial_chat = self._adversarial_chat or get_default_adversarial_target()

        atomic_attacks: list[AtomicAttack] = []
        for technique_name in selected_techniques:
            factory = factories.get(technique_name)
            if factory is None:
                logger.warning(f"No factory for technique '{technique_name}', skipping.")
                continue

            # Only pass scorer override if the technique accepts it.
            # Some techniques (e.g. TAP) manage their own scoring internally.
            scoring_for_technique = scoring_config if registry.accepts_scorer_override(technique_name) else None

            # Build adversarial config override if scenario has a custom adversarial target
            adversarial_override = None
            if self._adversarial_chat is not None:
                adversarial_override = AttackAdversarialConfig(target=self._adversarial_chat)

            for dataset_name, seed_groups in seed_groups_by_dataset.items():
                # Each AtomicAttack gets a fresh, independent attack instance
                attack_technique = factory.create(
                    objective_target=self._objective_target,
                    attack_scoring_config_override=scoring_for_technique,
                    attack_adversarial_config_override=adversarial_override,
                )
                display_group = self._build_display_group(
                    technique_name=technique_name,
                    seed_group_name=dataset_name,
                )
                atomic_attacks.append(
                    AtomicAttack(
                        atomic_attack_name=f"{technique_name}_{dataset_name}",
                        attack_technique=attack_technique,
                        seed_groups=list(seed_groups),
                        adversarial_chat=adversarial_chat,
                        objective_scorer=self._objective_scorer,
                        memory_labels=self._memory_labels,
                        display_group=display_group,
                    )
                )

        return atomic_attacks
