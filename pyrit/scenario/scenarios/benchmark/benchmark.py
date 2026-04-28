# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Benchmark scenario — compare adversarial-model ASR across attack techniques.

Strategies select **attack techniques** that use an adversarial chat model
(RolePlay, TAP).  The constructor takes a ``dict[str, PromptChatTarget]``
mapping user-chosen labels to adversarial targets.  At attack-creation time
each model is injected via ``attack_adversarial_config_override``, producing
a technique × model × dataset cross-product for side-by-side comparison.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar, cast

from pyrit.common import apply_defaults
from pyrit.executor.attack import RolePlayAttack, RolePlayPaths, TreeOfAttacksWithPruningAttack
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry, AttackTechniqueSpec
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptChatTarget
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Benchmark technique catalog — adversarial-capable techniques only
# ---------------------------------------------------------------------------
# These specs intentionally have NO adversarial_chat set.  The adversarial
# model is injected at create-time via attack_adversarial_config_override,
# keeping the spec list static and registry-independent.

BENCHMARK_TECHNIQUES: list[AttackTechniqueSpec] = [
    AttackTechniqueSpec(
        name="role_play",
        attack_class=RolePlayAttack,
        strategy_tags=["core", "single_turn"],
        extra_kwargs={"role_play_definition_path": RolePlayPaths.MOVIE_SCRIPT.value},
    ),
    AttackTechniqueSpec(
        name="tap",
        attack_class=TreeOfAttacksWithPruningAttack,
        strategy_tags=["core", "multi_turn"],
        accepts_scorer_override=False,
    ),
]


def _build_benchmark_strategy() -> type[ScenarioStrategy]:
    """
    Build the BenchmarkStrategy enum from ``BENCHMARK_TECHNIQUES``.

    Returns a strategy class whose concrete members are adversarial-capable
    techniques and whose aggregates allow selecting by turn style.

    Returns:
        type[ScenarioStrategy]: The dynamically generated strategy enum class.
    """
    return AttackTechniqueRegistry.build_strategy_class_from_specs(
        class_name="BenchmarkStrategy",
        specs=TagQuery.all("core").filter(BENCHMARK_TECHNIQUES),
        aggregate_tags={
            "all": TagQuery.any_of("core"),
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
    )


class Benchmark(Scenario):
    """
    Benchmarking scenario that compares the ASR of several adversarial models.

    Each selected technique is executed once per adversarial model per dataset,
    producing a cross-product of atomic attacks.  Results are grouped by model
    label so that ASR can be compared side-by-side.
    """

    VERSION: int = 1
    _cached_strategy_class: ClassVar[type[ScenarioStrategy] | None] = None

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Return the BenchmarkStrategy enum, building on first access.

        Returns:
            type[ScenarioStrategy]: The BenchmarkStrategy enum class.
        """
        if cls._cached_strategy_class is None:
            cls._cached_strategy_class = _build_benchmark_strategy()
        return cls._cached_strategy_class

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Return the default strategy (``ALL`` — run every benchmark technique).

        Returns:
            ScenarioStrategy: The ``all`` aggregate member.
        """
        return cls.get_strategy_class()("all")

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for benchmarking.

        Returns:
            DatasetConfiguration: Configuration with the HarmBench dataset.
        """
        return DatasetConfiguration(
            dataset_names=["harmbench"],
            max_dataset_size=8,
        )

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_models: dict[str, PromptChatTarget],
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the Benchmark scenario.

        Args:
            adversarial_models: Mapping of user-chosen label → adversarial
                chat target.  Each model will be benchmarked across all
                selected techniques and datasets.
            objective_scorer: Scorer for evaluating attack success.
                Defaults to the registered default objective scorer.
            scenario_result_id: Optional ID of an existing scenario
                result to resume.

        Raises:
            ValueError: If ``adversarial_models`` is empty.
        """
        if not adversarial_models:
            raise ValueError("adversarial_models must be a non-empty dict mapping labels to PromptChatTarget instances.")

        self._adversarial_models = dict(adversarial_models)
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=self.get_strategy_class(),
            scenario_result_id=scenario_result_id,
        )

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build atomic attacks from the cross-product of techniques × models × datasets.

        Factories are built locally from ``BENCHMARK_TECHNIQUES`` (not the
        registry singleton).  Each model is injected at create-time via
        ``attack_adversarial_config_override``.

        Returns:
            list[AtomicAttack]: One atomic attack per technique/model/dataset combination.

        Raises:
            ValueError: If the scenario has not been initialized.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig

        local_factories = {
            spec.name: AttackTechniqueRegistry.build_factory_from_spec(spec) for spec in BENCHMARK_TECHNIQUES
        }
        scorer_override_map = {spec.name: spec.accepts_scorer_override for spec in BENCHMARK_TECHNIQUES}

        selected_techniques = {s.value for s in self._scenario_strategies}
        seed_groups_by_dataset = self._dataset_config.get_seed_attack_groups()
        scoring_config = AttackScoringConfig(objective_scorer=cast("TrueFalseScorer", self._objective_scorer))

        atomic_attacks: list[AtomicAttack] = []
        for technique_name in selected_techniques:
            factory = local_factories.get(technique_name)
            if factory is None:
                logger.warning("No factory for technique '%s', skipping.", technique_name)
                continue

            scoring_for_technique = scoring_config if scorer_override_map.get(technique_name, True) else None

            for model_label, model_target in self._adversarial_models.items():
                adv_config = AttackAdversarialConfig(target=model_target)

                for dataset_name, seed_groups in seed_groups_by_dataset.items():
                    attack_technique = factory.create(
                        objective_target=self._objective_target,
                        attack_adversarial_config_override=adv_config,
                        attack_scoring_config_override=scoring_for_technique,
                    )
                    atomic_attacks.append(
                        AtomicAttack(
                            atomic_attack_name=f"{technique_name}__{model_label}_{dataset_name}",
                            attack_technique=attack_technique,
                            seed_groups=list(seed_groups),
                            adversarial_chat=model_target,
                            objective_scorer=cast("TrueFalseScorer", self._objective_scorer),
                            memory_labels=self._memory_labels,
                            display_group=model_label,
                        )
                    )

        return atomic_attacks
