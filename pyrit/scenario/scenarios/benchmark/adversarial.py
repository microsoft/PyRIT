# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AdversarialBenchmark scenario — compare attack success rate across adversarial models."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS
from pyrit.registry import AttackTechniqueRegistry
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


class AdversarialBenchmark(Scenario):
    """
    Benchmarking scenario that compares the attack success rate (ASR)
    of several different adversarial models.
    """

    VERSION: int = 1
    _cached_strategy_class: ClassVar[type[ScenarioStrategy] | None] = None

    #: AdversarialBenchmark compares attack-success rates across adversarial models; a baseline
    #: attack would be model-independent and contribute no signal to the comparison.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Return the AdversarialBenchmarkStrategy enum, building on first access.

        Returns:
            type[ScenarioStrategy]: The BenchmarkStrategy enum class.
        """
        if cls._cached_strategy_class is None:
            cls._cached_strategy_class = AdversarialBenchmark._build_benchmark_strategy()

        return cls._cached_strategy_class

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Return the default strategy (``light`` — run benchmark-friendly techniques
        that can wrap up quickly and without too many system resources).

        Returns:
            ScenarioStrategy: The ``light`` aggregate member.
        """
        return cls.get_strategy_class()("light")

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for benchmarking.

        Returns:
            DatasetConfiguration: Configuration with standard harm-category datasets.
        """
        return DatasetConfiguration(
            dataset_names=["harmbench"],
            max_dataset_size=8,
        )

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_models: list[PromptTarget],
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the AdversarialBenchmark scenario.

        Args:
            adversarial_models: A non-empty list of ``PromptTarget`` instances
                that each satisfy ``CHAT_TARGET_REQUIREMENTS`` (multi-turn
                with editable history).  Individual techniques selected at
                run time may impose stricter capability requirements which are
                enforced when their attack instances are constructed.
                Labels are inferred from each target's identifier (preferring
                ``underlying_model_name`` over ``model_name`` over the class
                name).  Identical targets are silently deduped and distinct
                targets whose inferred names collide are suffixed (``_2``,
                ``_3``, …) with a warning.
            objective_scorer: Scorer for evaluating attack success.
                Defaults to the registered default objective scorer.
            scenario_result_id: Optional ID of an existing scenario
                result to resume.

        Raises:
            ValueError: If ``adversarial_models`` is empty, not a list, or
                contains a target that does not satisfy
                ``CHAT_TARGET_REQUIREMENTS``.
        """
        if not adversarial_models:
            raise ValueError("adversarial_models must be a non-empty list of PromptTarget instances.")

        if not isinstance(adversarial_models, list):
            raise ValueError("adversarial_models must be a list of PromptTarget instances.")

        for target in adversarial_models:
            try:
                CHAT_TARGET_REQUIREMENTS.validate(target=target)
            except ValueError as exc:
                raise ValueError(
                    f"adversarial_models entry {type(target).__name__} does not satisfy "
                    f"the chat-target capability requirements: {exc}"
                ) from exc

        # Infer labels, then wrap each bare target in a default AttackAdversarialConfig
        # so it can be passed to factory.create() as an override.
        labeled_targets = self._infer_labels(items=adversarial_models)
        self._adversarial_configs: dict[str, AttackAdversarialConfig] = {
            label: AttackAdversarialConfig(target=target) for label, target in labeled_targets.items()
        }

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

        Factories are read from the singleton ``AttackTechniqueRegistry`` and
        narrowed to adversarial-capable ones. Each model is injected at
        create-time via ``attack_adversarial_config_override``.

        Returns:
            list[AtomicAttack]: One atomic attack per technique/model/dataset combination.

        Raises:
            ValueError: If the scenario has not been initialized.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        benchmarkable_factories = AdversarialBenchmark._get_benchmarkable_factories()
        local_factories = {factory.name: factory for factory in benchmarkable_factories}

        selected_techniques = {s.value for s in self._scenario_strategies}
        seed_groups_by_dataset = self._dataset_config.get_seed_attack_groups()
        scoring_config = AttackScoringConfig(objective_scorer=self._objective_scorer)

        atomic_attacks: list[AtomicAttack] = []
        for technique_name in selected_techniques:
            factory = local_factories.get(technique_name)
            if factory is None:
                logger.warning("No factory for technique '%s', skipping.", technique_name)
                continue

            for model_label, adv_config in self._adversarial_configs.items():
                for dataset_name, seed_groups in seed_groups_by_dataset.items():
                    attack_technique = factory.create(
                        objective_target=self._objective_target,
                        attack_scoring_config=scoring_config,
                        attack_adversarial_config_override=adv_config,
                    )
                    atomic_attacks.append(
                        AtomicAttack(
                            atomic_attack_name=f"{technique_name}__{model_label}__{dataset_name}",
                            attack_technique=attack_technique,
                            seed_groups=list(seed_groups),
                            adversarial_chat=adv_config.target,
                            objective_scorer=self._objective_scorer,
                            memory_labels=self._memory_labels,
                            display_group=model_label,
                        )
                    )

        return atomic_attacks

    @staticmethod
    def _infer_labels(
        *,
        items: list[PromptTarget],
    ) -> dict[str, PromptTarget]:
        """
        Infer user-facing labels for a list of adversarial targets.

        The dedupe key is ``target.get_identifier().hash`` so identical
        targets collapse to a single entry silently, while two distinct
        targets whose inferred names happen to match get a numeric suffix
        and a ``logger.warning`` so the situation isn't silent.

        Args:
            items: List of ``PromptTarget`` instances.

        Returns:
            dict[str, PromptTarget]: Mapping from inferred label to the
                original target.  Targets are wrapped in an
                ``AttackAdversarialConfig`` by ``__init__`` after this call.
        """
        result: dict[str, PromptTarget] = {}
        seen_keys: dict[str, str | None] = {}

        for target in items:
            identifier = target.get_identifier()
            params = identifier.params or {}
            base_name = params.get("underlying_model_name") or params.get("model_name") or type(target).__name__

            dedupe_key = identifier.hash

            # Identical target already stored under some label — silently drop.
            if dedupe_key in seen_keys.values():
                continue

            if base_name not in seen_keys:
                result[base_name] = target
                seen_keys[base_name] = dedupe_key
                continue

            # Distinct target colliding on inferred name — find next free suffix and warn.
            counter = 2
            while f"{base_name}_{counter}" in seen_keys:
                counter += 1
            suffixed = f"{base_name}_{counter}"
            logger.warning(
                "Inferred label '%s' collided with a different model setup; using '%s' instead.",
                base_name,
                suffixed,
            )
            result[suffixed] = target
            seen_keys[suffixed] = dedupe_key

        return result

    @staticmethod
    def _build_benchmark_strategy() -> type[ScenarioStrategy]:
        """
        Build the BenchmarkStrategy enum from adversarial-capable factories.

        Returns a strategy class whose concrete members are adversarial-capable
        techniques and whose aggregates allow selecting by turn style.

        Returns:
            type[ScenarioStrategy]: The dynamically generated strategy enum class.
        """
        return AttackTechniqueRegistry.build_strategy_class_from_factories(  # type: ignore[ty:invalid-return-type]
            class_name="BenchmarkStrategy",
            factories=AdversarialBenchmark._get_benchmarkable_factories(),
            aggregate_tags={
                "default": TagQuery.any_of("default"),
                "single_turn": TagQuery.any_of("single_turn"),
                "multi_turn": TagQuery.any_of("multi_turn"),
                "light": TagQuery.any_of("light"),
            },
        )

    @staticmethod
    def _get_benchmarkable_factories() -> list[AttackTechniqueFactory]:
        """
        Return ``core`` factories that drive an adversarial chat.

        Every benchmark technique must accept an adversarial-config override at
        ``create()`` time so the scenario can inject one chat per benchmark
        model. We narrow to the ``core`` tag to exclude experimental / persona
        variants.

        Returns:
            list[AttackTechniqueFactory]: Filtered core, adversarial-capable factories.
        """
        registry = AttackTechniqueRegistry.get_registry_singleton()
        return [
            factory
            for factory in registry.get_factories_or_raise().values()
            if factory.uses_adversarial and "core" in factory.strategy_tags
        ]
