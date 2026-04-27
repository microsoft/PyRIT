# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar, cast

from pyrit.common import apply_defaults
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry, AttackTechniqueSpec
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.prompt_target import PromptChatTarget
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


class Benchmark(Scenario):
    """
    Benchmarking scenario that compares the ASR of several different adversarial models.
    """

    VERSION: int = 1
    _cached_strategy_class: ClassVar[type[ScenarioStrategy] | None] = None

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Return the dynamically generated strategy class, building it on first access.

        When called as a classmethod (e.g. from ScenarioRegistry), this returns a
        strategy built from the unmodified adversarial-capable SCENARIO_TECHNIQUES
        without any live adversarial targets. The instance-specific strategy class
        with live targets is built in ``__init__`` and passed to ``super().__init__``.

        Returns:
            type[ScenarioStrategy]: The BenchmarkStrategy enum class.
        """
        if cls._cached_strategy_class is None:
            strategy, _, _ = Benchmark._build_benchmark_strategy()
            cls._cached_strategy_class = strategy
        return cls._cached_strategy_class

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Return the default strategy member (``DEFAULT``).

        Returns:
            ScenarioStrategy: The default strategy value.
        """
        strategy_class = cls.get_strategy_class()
        return strategy_class("default")

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
        adversarial_models: list[PromptChatTarget],
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the Benchmark scenario.

        Args:
            adversarial_models (list[PromptChatTarget]): Adversarial models to benchmark.
            scenario_result_id (str | None): Optional ID of an existing scenario
                result to resume.

        Raises:
            ValueError: If adversarial_models is empty.
        """
        if not adversarial_models:
            raise ValueError("adversarial_models must be a non-empty list of PromptChatTarget instances.")

        self._objective_scorer = self._get_default_objective_scorer()

        strategy, technique_to_model, benchmark_specs = Benchmark._build_benchmark_strategy(adversarial_models)
        self._technique_to_model: dict[str, str] = technique_to_model
        self._benchmark_specs = benchmark_specs

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=strategy,
            scenario_result_id=scenario_result_id,
        )

    def _prepare_strategies(
        self,
        strategies: Sequence[ScenarioStrategy] | None,
    ) -> list[ScenarioStrategy]:
        """
        Resolve strategy inputs using the instance-specific strategy class.

        Overrides the base implementation to avoid calling ``get_default_strategy()``
        (a classmethod that returns a member from the blank strategy class). Instead,
        resolves the default from ``self._strategy_class`` directly.

        Call stack::

            initialize_async()           [Scenario base — scenario.py]
              → _prepare_strategies()    [Benchmark override — this method]
                  → self._strategy_class.resolve()

        Why override:
            The base ``_prepare_strategies`` calls ``self.get_default_strategy()``,
            which is a classmethod returning a member from the *blank* strategy
            enum (built without adversarial models). That member belongs to a
            different enum class than ``self._strategy_class`` (built with live
            adversarial models in ``__init__``), causing ``resolve()`` to skip it.
            This override uses ``self._strategy_class("default")`` to get the
            correct default member from the instance-specific enum.

        Args:
            strategies (Sequence[ScenarioStrategy] | None): Strategy inputs from
                initialize_async. None or [] both mean use default.

        Returns:
            list[ScenarioStrategy]: Ordered, deduplicated concrete strategies.
        """
        default = self._strategy_class("default")
        return self._strategy_class.resolve(strategies, default=default)

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build atomic attacks from the cross-product of permuted techniques and datasets.

        Overrides the base implementation because the base uses the singleton
        ``AttackTechniqueRegistry``, which would either miss our permuted techniques
        or cause stale-target bugs across multiple Benchmark instances. Instead,
        builds factories locally from ``self._benchmark_specs`` using
        ``AttackTechniqueRegistry.build_factory_from_spec`` (a static method that
        does not touch the singleton).

        Call stack::

            initialize_async()                    [Scenario base — scenario.py]
              → _get_atomic_attacks_async()        [Benchmark override — this method]
                  → build_factory_from_spec()      [static, no singleton]
                  → factory.create()               [produces AttackTechnique]
                  → _build_display_group()          [Benchmark override]
                  → AtomicAttack(...)              [one per technique × dataset]

        Why override:
            The base ``_get_atomic_attacks_async`` calls
            ``_get_attack_technique_factories()`` which registers techniques into
            the global ``AttackTechniqueRegistry`` singleton.  Benchmark's permuted
            techniques (e.g. ``tap__gpt4o``) are instance-specific and must not
            pollute the singleton — doing so would cause stale-target bugs when
            multiple Benchmark instances exist in one process.  This override
            builds factories locally using the same ``build_factory_from_spec``
            static method but stores them in a local dict.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.

        Raises:
            ValueError: If the scenario has not been initialized.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        from pyrit.executor.attack import AttackScoringConfig

        local_factories = {
            spec.name: AttackTechniqueRegistry.build_factory_from_spec(spec) for spec in self._benchmark_specs
        }
        scorer_override_map = {spec.name: spec.accepts_scorer_override for spec in self._benchmark_specs}

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

            for dataset_name, seed_groups in seed_groups_by_dataset.items():
                attack_technique = factory.create(
                    objective_target=self._objective_target,
                    attack_scoring_config_override=scoring_for_technique,
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
                        adversarial_chat=factory.adversarial_chat,
                        objective_scorer=cast("TrueFalseScorer", self._objective_scorer),
                        memory_labels=self._memory_labels,
                        display_group=display_group,
                    )
                )

        return atomic_attacks

    def _build_display_group(self, *, technique_name: str, seed_group_name: str) -> str:
        """
        Build display-group label for an atomic attack.

        Groups results by adversarial model identifier rather than by technique
        or dataset, enabling side-by-side ASR comparison across models.

        Args:
            technique_name (str): Attack technique name (e.g. ``"tap__gpt4o"``).
            seed_group_name (str): Seed group name (e.g. ``"harmbench"``).

        Returns:
            str: The adversarial model label for this technique.
        """
        return self._technique_to_model[technique_name]

    @staticmethod
    def _resolve_model_label(model: PromptChatTarget) -> str:
        """
        Derive a human-readable label from a PromptChatTarget.

        Tries ``_model_name`` first, then falls back to the component
        identifier's ``unique_name``.

        Args:
            model (PromptChatTarget): The adversarial model target.

        Returns:
            str: A label suitable for spec naming and display grouping.
        """
        # _model_name is private but has no public accessor; flagged for follow-up.
        if model._model_name:
            return model._model_name
        return model.get_identifier().unique_name

    @staticmethod
    def _build_benchmark_strategy(
        adversarial_models: list[PromptChatTarget] | None = None,
    ) -> tuple[type[ScenarioStrategy], dict[str, str], list[AttackTechniqueSpec]]:
        """
        Build the Benchmark strategy class dynamically from SCENARIO_TECHNIQUES.

        Filters SCENARIO_TECHNIQUES to adversarial-capable techniques (those whose
        attack class accepts ``attack_adversarial_config``), then permutes each with
        every adversarial model to produce unique specs.

        When called without adversarial_models (e.g. from ``get_strategy_class``),
        returns a strategy built from the unpermuted adversarial-capable techniques.

        Args:
            adversarial_models (list[PromptChatTarget] | None): Adversarial models to
                permute with techniques. None produces a blank strategy for class-level use.

        Returns:
            tuple: (strategy_class, technique_to_model_mapping, permuted_specs).
        """
        filtered_techniques = [
            s for s in SCENARIO_TECHNIQUES if AttackTechniqueRegistry._accepts_adversarial(s.attack_class)
        ]
        technique_to_model: dict[str, str] = {}
        permuted_specs: list[AttackTechniqueSpec] = list(filtered_techniques)

        if adversarial_models:
            permuted_specs = []
            for model in adversarial_models:
                model_label = Benchmark._resolve_model_label(model)
                for technique in filtered_techniques:
                    technique_name = f"{technique.name}__{model_label}"

                    permuted_specs.append(
                        replace(
                            technique,
                            name=technique_name,
                            adversarial_chat=model,
                        )
                    )
                    technique_to_model[technique_name] = model_label

        strategy_class = AttackTechniqueRegistry.build_strategy_class_from_specs(
            class_name="BenchmarkStrategy",
            specs=TagQuery.all("core").filter(permuted_specs),
            aggregate_tags={
                "default": TagQuery.any_of("default"),
                "multi_turn": TagQuery.any_of("multi_turn"),
            },
        )

        return strategy_class, technique_to_model, permuted_specs
