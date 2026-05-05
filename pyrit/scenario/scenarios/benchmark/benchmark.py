# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Benchmark scenario — compare adversarial-model attack success rate (ASR)
across attack techniques.

Strategies are built dynamically by filtering ``SCENARIO_TECHNIQUES`` to those
that accept an adversarial chat model but don't have one baked in.  The
constructor takes either a ``dict`` mapping user-chosen labels to adversarial
targets/configs, or a plain ``list`` (labels inferred from each target's
identifier).  Internally everything is normalized to
``dict[str, AttackAdversarialConfig]`` so per-model system prompts and seed
prompts are preserved.

At attack-creation time each config is injected via
``attack_adversarial_config_override``, producing a technique × model × dataset
cross-product for side-by-side comparison.

New adversarial techniques added to ``SCENARIO_TECHNIQUES`` are automatically
discovered — no changes to this module needed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
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


class Benchmark(Scenario):
    """
    Benchmarking scenario that compares the attack success rate (ASR)
    of several different adversarial models.
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
            cls._cached_strategy_class = Benchmark._build_benchmark_strategy()

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
        adversarial_models: (
            dict[str, PromptChatTarget | AttackAdversarialConfig] | list[PromptChatTarget | AttackAdversarialConfig]
        ),
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the Benchmark scenario.

        Args:
            adversarial_models: Either a ``dict`` mapping user-chosen labels to
                a ``PromptChatTarget`` or an ``AttackAdversarialConfig``, or a
                ``list`` of the same element types.  When a list is given,
                labels are inferred from each target's identifier; identical
                setups are silently deduped and merely-name-colliding distinct
                setups are suffixed (``_2``, ``_3``, …) with a warning.  Bare
                targets are wrapped in a default ``AttackAdversarialConfig`` so
                a per-model ``system_prompt_path`` / ``seed_prompt`` can be
                supplied via the config form.
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
                "PromptChatTarget/AttackAdversarialConfig instances, or a non-empty list "
                "from which labels will be inferred."
            )

        # Stage A: list → dict (with inferred, deduped labels).
        if isinstance(adversarial_models, list):
            adversarial_models = self._infer_labels(items=adversarial_models)

        if not isinstance(adversarial_models, dict):
            raise ValueError(
                "adversarial_models must be a dict or a list of PromptChatTarget/AttackAdversarialConfig instances."
            )

        if "" in adversarial_models:
            raise ValueError(f"Empty user-chosen label passed to adversarial_models! Got `{adversarial_models}`.")

        # Stage B: dict[str, target | config] → dict[str, AttackAdversarialConfig].
        # Bare targets are wrapped; existing configs (with their system_prompt_path /
        # seed_prompt) pass through unchanged.
        self._adversarial_configs: dict[str, AttackAdversarialConfig] = {
            label: (value if isinstance(value, AttackAdversarialConfig) else AttackAdversarialConfig(target=value))
            for label, value in adversarial_models.items()
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

        benchmarkable_specs = Benchmark._get_benchmarkable_specs()
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
        items: list[PromptChatTarget | AttackAdversarialConfig],
    ) -> dict[str, PromptChatTarget | AttackAdversarialConfig]:
        """
        Infer user-facing labels for a list of targets/configs.

        The dedupe key is ``(target.get_identifier().hash, system_prompt_path,
        seed_prompt)`` so identical experiments collapse to a single entry
        silently, while two distinct setups whose inferred names happen to
        match get a numeric suffix and a ``logger.warning`` so the situation
        isn't silent.

        Args:
            items: List of bare ``PromptChatTarget`` or ``AttackAdversarialConfig``.

        Returns:
            dict[str, PromptChatTarget | AttackAdversarialConfig]: Mapping from
                inferred label to the original item (configs pass through; bare
                targets are wrapped later by Stage B in ``__init__``).
        """
        result: dict[str, PromptChatTarget | AttackAdversarialConfig] = {}
        seen_keys: dict[str, tuple[str | None, str, str]] = {}

        for item in items:
            # Wrap purely to read defaults (system_prompt_path, seed_prompt).
            cfg_for_key = item if isinstance(item, AttackAdversarialConfig) else AttackAdversarialConfig(target=item)

            target = cfg_for_key.target
            identifier = target.get_identifier()
            params = identifier.params or {}
            base_name = params.get("underlying_model_name") or params.get("model_name") or type(target).__name__

            dedupe_key: tuple[str | None, str, str] = (
                identifier.hash,
                str(cfg_for_key.system_prompt_path) if cfg_for_key.system_prompt_path is not None else "",
                repr(cfg_for_key.seed_prompt),
            )

            # Identical setup already stored under some label — silently drop.
            if dedupe_key in seen_keys.values():
                continue

            if base_name not in seen_keys:
                result[base_name] = item
                seen_keys[base_name] = dedupe_key
                continue

            # Distinct setup colliding on inferred name — find next free suffix and warn.
            counter = 2
            while f"{base_name}_{counter}" in seen_keys:
                counter += 1
            suffixed = f"{base_name}_{counter}"
            logger.warning(
                "Inferred label '%s' collided with a different model setup; using '%s' instead.",
                base_name,
                suffixed,
            )
            result[suffixed] = item
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
        specs = Benchmark._get_benchmarkable_specs()
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
