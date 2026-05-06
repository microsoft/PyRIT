# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AdversarialBenchmark scenario — compare adversarial-model attack success rate (ASR)
across attack techniques.

Strategies are built dynamically by filtering ``SCENARIO_TECHNIQUES`` to those
that accept an adversarial chat model but don't have one baked in.  The
constructor takes either a ``dict`` mapping user-chosen labels to
``PromptChatTarget`` instances, or a plain ``list`` of targets (labels inferred
from each target's identifier).  Each target is wrapped in a default
``AttackAdversarialConfig`` and injected at attack-creation time via
``attack_adversarial_config_override``, producing a technique × model × dataset
cross-product for side-by-side comparison.

New adversarial techniques added to ``SCENARIO_TECHNIQUES`` are automatically
discovered — no changes to this module needed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.common.parameter import Parameter
from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig
from pyrit.registry import AttackTechniqueRegistry, AttackTechniqueSpec
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptChatTarget
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

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare custom parameters this scenario accepts from the CLI / config file.

        Returns:
            list[Parameter]: Parameters configurable per-run.
        """
        return [
            Parameter(
                name="include_default_baseline",
                description=(
                    "Whether to include a baseline atomic attack that sends each objective "
                    "unmodified through every selected adversarial model."
                ),
                param_type=bool,
                default=False,
            ),
        ]

    @apply_defaults
    def __init__(
        self,
        *,
        adversarial_models: dict[str, PromptChatTarget] | list[PromptChatTarget],
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the AdversarialBenchmark scenario.

        Args:
            adversarial_models: Either a ``dict`` mapping user-chosen labels to
                ``PromptChatTarget`` instances, or a ``list`` of targets (labels
                inferred from each target's identifier).  When a list is given,
                identical targets are silently deduped and distinct targets
                whose inferred names collide are suffixed (``_2``, ``_3``, …)
                with a warning.  Each target is wrapped in a default
                ``AttackAdversarialConfig`` before being injected into each
                technique.
            objective_scorer: Scorer for evaluating attack success.
                Defaults to the registered default objective scorer.
            scenario_result_id: Optional ID of an existing scenario
                result to resume.

        Raises:
            ValueError: If ``adversarial_models`` is empty, an unsupported
                type, or contains an empty-string label.
        """
        if not adversarial_models:
            raise ValueError(
                "adversarial_models must be a non-empty dict mapping labels to "
                "PromptChatTarget instances, or a non-empty list from which labels "
                "will be inferred."
            )

        # Stage A: list → dict (with inferred, deduped labels).
        if isinstance(adversarial_models, list):
            adversarial_models = self._infer_labels(items=adversarial_models)

        if not isinstance(adversarial_models, dict):
            raise ValueError("adversarial_models must be a dict or a list of PromptChatTarget instances.")

        if "" in adversarial_models:
            raise ValueError(f"Empty user-chosen label passed to adversarial_models! Got `{adversarial_models}`.")

        # Stage B: wrap each bare target in a default AttackAdversarialConfig.
        self._adversarial_configs: dict[str, AttackAdversarialConfig] = {
            label: AttackAdversarialConfig(target=target) for label, target in adversarial_models.items()
        }

        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=self.get_strategy_class(),
            include_default_baseline=False,
            scenario_result_id=scenario_result_id,
        )

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build atomic attacks from the cross-product of techniques × models × datasets.

        Factories are built locally from adversarial-capable ``SCENARIO_TECHNIQUES``
        (not the registry singleton).  Each model is injected at create-time via
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

        # Sync the include_default_baseline param into the base-class flag.  The
        # base class reads ``self._include_baseline`` immediately after this method
        # returns, and ``set_params_from_args`` has already run by this point so
        # ``self.params["include_default_baseline"]`` is guaranteed to be set.
        self._include_baseline = self.params.get("include_default_baseline", False)

        benchmarkable_specs = AdversarialBenchmark._get_benchmarkable_specs()
        local_factories = {
            spec.name: AttackTechniqueRegistry.build_factory_from_spec(spec) for spec in benchmarkable_specs
        }
        scorer_override_map = {spec.name: spec.accepts_scorer_override for spec in benchmarkable_specs}

        selected_techniques = {s.value for s in self._scenario_strategies}
        seed_groups_by_dataset = self._dataset_config.get_seed_attack_groups()
        scoring_config = AttackScoringConfig(objective_scorer=self._objective_scorer)

        atomic_attacks: list[AtomicAttack] = []
        for technique_name in selected_techniques:
            factory = local_factories.get(technique_name)
            if factory is None:
                logger.warning("No factory for technique '%s', skipping.", technique_name)
                continue

            scoring_for_technique = scoring_config if scorer_override_map.get(technique_name, True) else None

            for model_label, adv_config in self._adversarial_configs.items():
                for dataset_name, seed_groups in seed_groups_by_dataset.items():
                    attack_technique = factory.create(
                        objective_target=self._objective_target,
                        attack_adversarial_config_override=adv_config,
                        attack_scoring_config_override=scoring_for_technique,
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
        items: list[PromptChatTarget],
    ) -> dict[str, PromptChatTarget]:
        """
        Infer user-facing labels for a list of adversarial targets.

        The dedupe key is ``target.get_identifier().hash`` so identical
        targets collapse to a single entry silently, while two distinct
        targets whose inferred names happen to match get a numeric suffix
        and a ``logger.warning`` so the situation isn't silent.

        Args:
            items: List of ``PromptChatTarget`` instances.

        Returns:
            dict[str, PromptChatTarget]: Mapping from inferred label to the
                original target.  Targets are wrapped in an
                ``AttackAdversarialConfig`` later by Stage B in ``__init__``.
        """
        result: dict[str, PromptChatTarget] = {}
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
        Build the BenchmarkStrategy enum from adversarial-capable ``SCENARIO_TECHNIQUES``.

        Returns a strategy class whose concrete members are adversarial-capable
        techniques (no baked-in adversarial chat) and whose aggregates allow
        selecting by turn style.

        Returns:
            type[ScenarioStrategy]: The dynamically generated strategy enum class.
        """
        specs = AdversarialBenchmark._get_benchmarkable_specs()
        return AttackTechniqueRegistry.build_strategy_class_from_specs(  # type: ignore[ty:invalid-return-type]
            class_name="BenchmarkStrategy",
            specs=TagQuery.all("core").filter(specs),
            aggregate_tags={
                "default": TagQuery.any_of("default"),
                "single_turn": TagQuery.any_of("single_turn"),
                "multi_turn": TagQuery.any_of("multi_turn"),
                "light": TagQuery.any_of("light"),
            },
        )

    @staticmethod
    def _get_benchmarkable_specs() -> list[AttackTechniqueSpec]:
        """
        Return techniques from ``SCENARIO_TECHNIQUES`` that accept an adversarial
        model but don't have one already baked in.

        This is the dual guard: ``_accepts_adversarial`` ensures the technique
        CAN use an adversarial model, and ``adversarial_chat is None`` ensures
        it doesn't already have one set — we inject our own at create-time.

        Returns:
            list[AttackTechniqueSpec]: Filtered, adversarial-ready specs.
        """
        return [
            spec
            for spec in SCENARIO_TECHNIQUES
            if AttackTechniqueRegistry._accepts_adversarial(spec.attack_class) and spec.adversarial_chat is None
        ]
