# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AdversarialBenchmark scenario — compare attack success rate across adversarial models."""

from __future__ import annotations

import dataclasses
import logging
from functools import cache
from typing import TYPE_CHECKING, ClassVar

<<<<<<< HEAD
from pyrit.analytics import get_cached_results_for_technique
from pyrit.common import Parameter, apply_defaults
from pyrit.executor.attack import AttackScoringConfig
from pyrit.identifiers import ObjectiveTargetEvaluationIdentifier
from pyrit.models import AttackOutcome, SeedAttackGroup
from pyrit.registry import AttackTechniqueRegistry, TargetRegistry
=======
from pyrit.common import apply_defaults
from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS
from pyrit.registry import AttackTechniqueRegistry
>>>>>>> 4cf9ff5de64017ce09cbe9eeb653a35a9983cab4
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
<<<<<<< HEAD
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES, _spec_needs_adversarial
=======
>>>>>>> 4cf9ff5de64017ce09cbe9eeb653a35a9983cab4

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


logger = logging.getLogger(__name__)


def _build_benchmark_strategy() -> type[ScenarioStrategy]:
    """
    Build the ``BenchmarkStrategy`` enum from ``SCENARIO_TECHNIQUES``.

    Filters the static technique catalog to entries that require an
    adversarial chat target (per ``_spec_needs_adversarial``) and passes
    those source specs to
    ``AttackTechniqueRegistry.build_strategy_class_from_specs``. The
    resulting enum has one concrete member per source technique (e.g.
    ``red_teaming``, ``tap``, ``crescendo_simulated``) plus the standard
    ``all`` / ``light`` / ``single_turn`` / ``multi_turn`` aggregates inherited
    from the source specs' ``strategy_tags``.

    The (technique × target) cross-product is no longer pre-materialized into
    enum members; per-target factories are built lazily in
    ``AdversarialBenchmark._get_atomic_attacks_async`` from the
    user-supplied ``adversarial_targets`` parameter.

    Returns:
        type[ScenarioStrategy]: The dynamically generated ``BenchmarkStrategy`` class.
    """
    adversarial_specs = [spec for spec in SCENARIO_TECHNIQUES if _spec_needs_adversarial(spec)]

    return AttackTechniqueRegistry.build_strategy_class_from_specs(  # type: ignore[ty:invalid-return-type]
        class_name="BenchmarkStrategy",
        specs=adversarial_specs,
        aggregate_tags={
            "light": TagQuery.any_of("light"),
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
    )


class AdversarialBenchmark(Scenario):
    """
    Benchmark scenario that compares the attack success rate (ASR) across adversarial models.

    Adversarial targets are user-supplied via the ``adversarial_targets``
    parameter (declared in ``supported_parameters``). Each target must
    already be registered in ``TargetRegistry`` — typically by
    ``TargetInitializer`` from ``ADVERSARIAL_CHAT_*`` env vars, or
    programmatically via ``TargetRegistry.register_instance``.

    At run time, ``_get_atomic_attacks_async`` performs the
    ``(technique × adversarial_target × dataset)`` cross-product: for each
    selected adversarial-capable technique in ``SCENARIO_TECHNIQUES`` and
    each requested target, it constructs a per-pair
    ``AttackTechniqueFactory`` via
    ``AttackTechniqueRegistry.build_factory_from_spec`` with
    ``adversarial_chat`` overridden to that target — no global registry
    mutation. The resulting ``AtomicAttack`` is named
    ``f"{technique}__{target}_{dataset}"`` with ``display_group`` set to the
    target's registry name so per-model ASR rolls up naturally in result
    displays.
    """

<<<<<<< HEAD
    #: Bumped from 1 → 2 by the refactor that moved adversarial targets
    #: from a constructor parameter to the ``adversarial_targets`` scenario
    #: parameter and changed ``atomic_attack_name`` from
    #: ``{technique}__{model}__{dataset}`` to ``{technique}__{target}_{dataset}``.
    #: ``skip_cached`` only matches against prior runs at the current
    #: ``VERSION``; v1 results remain queryable but won't suppress v2 runs.
    VERSION: int = 2
=======
    VERSION: int = 1
>>>>>>> 4cf9ff5de64017ce09cbe9eeb653a35a9983cab4

    #: AdversarialBenchmark compares attack-success rates across adversarial models; a baseline
    #: attack would be model-independent and contribute no signal to the comparison.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

<<<<<<< HEAD
    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Return the ``BenchmarkStrategy`` enum.

        The enum is deterministic given the current ``SCENARIO_TECHNIQUES``
        catalog (the scenario no longer fans out across registry entries),
        so it is rebuilt on every call rather than cached.

        Returns:
            type[ScenarioStrategy]: The ``BenchmarkStrategy`` enum class.
        """
        return _build_benchmark_strategy()

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Return the default strategy (``light``).

        Returns:
            ScenarioStrategy: The ``light`` aggregate member — runs the
            subset of benchmark-friendly techniques that finish quickly with
            modest system resources (excludes ``tap`` and
            ``crescendo_simulated``, which can take hours on a single run).
        """
        return cls.get_strategy_class()("light")

    @classmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for benchmarking.

        Returns:
            DatasetConfiguration: ``harmbench`` capped at 8 prompts per
            atomic attack.
        """
        return DatasetConfiguration(
            dataset_names=["harmbench"],
            max_dataset_size=8,
        )

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare the ``adversarial_targets`` parameter.

        The list is treated as required at run time:
        ``_get_atomic_attacks_async`` raises ``ValueError`` if
        ``self.params["adversarial_targets"]`` is empty or missing. The
        scenario-side error (rather than a declaration-side default) lets
        the caller raise a domain-specific message that names the CLI flag,
        the ``.pyrit_conf`` key, and ``pyrit_scan list-targets``.

        Returns:
            list[Parameter]: Single parameter declaring
            ``adversarial_targets: list[str]``.
        """
        return [
            Parameter(
                name="adversarial_targets",
                description=(
                    "Registry names of adversarial chat targets to benchmark. "
                    "Each name must already be registered in TargetRegistry "
                    "(via TargetInitializer or TargetRegistry.register_instance). "
                    "Use 'pyrit_scan list-targets' to see registered targets. "
                    "Settable via --adversarial-targets <name> [<name> ...] on the CLI, "
                    "or scenario.args.adversarial_targets in .pyrit_conf."
                ),
                param_type=list[str],
                default=None,
            ),
        ]

=======
>>>>>>> 4cf9ff5de64017ce09cbe9eeb653a35a9983cab4
    @apply_defaults
    def __init__(
        self,
        *,
<<<<<<< HEAD
=======
        adversarial_models: list[PromptTarget] | None = None,
>>>>>>> 4cf9ff5de64017ce09cbe9eeb653a35a9983cab4
        objective_scorer: TrueFalseScorer | None = None,
        skip_cached: bool = False,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the AdversarialBenchmark scenario.

        Args:
<<<<<<< HEAD
            objective_scorer: ``TrueFalseScorer`` used to evaluate attack
                success. Defaults to the registered default objective
                scorer (typically the composite refusal+scale scorer set
                up by an initializer). Widening to general ``Scorer``
                support (covering ``FloatScaleScorer``, etc.) is tracked
                as a follow-up.
            skip_cached: When ``True``, ``_get_atomic_attacks_async`` filters
                out atomic attacks for which the live behavioral cache
                (``pyrit.analytics.get_cached_results_for_technique``) has
                already returned at least one ``SUCCESS`` or ``FAILURE``
                ``AttackResult`` for the matching
                ``(technique_eval_hash × objective_target_eval_hash)``
                pair. ``ERROR`` and ``UNDETERMINED`` outcomes never count
                as cache hits. The cache spans every prior run that
                produced the same (technique × objective target)
                combination — it is intentionally not scoped to this
                scenario name or ``VERSION``.
            scenario_result_id: Optional ID of an existing scenario result
                to resume.
        """
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )
        self._skip_cached: bool = skip_cached

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=self.get_strategy_class(),
            scenario_result_id=scenario_result_id,
        )
=======
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
                May be ``None`` at construction so the scenario can be
                introspected (e.g. for ``--list-scenarios`` metadata); the
                non-empty / capability validation is then deferred to
                ``initialize_async``.
            objective_scorer: Scorer for evaluating attack success.
                Defaults to the registered default objective scorer.
            scenario_result_id: Optional ID of an existing scenario
                result to resume.

        Raises:
            ValueError: If ``adversarial_models`` is provided and is empty,
                not a list, or contains a target that does not satisfy
                :data:`CHAT_TARGET_REQUIREMENTS`.
        """
        if adversarial_models is not None:
            self._adversarial_configs = self._build_adversarial_configs(adversarial_models)
        else:
            self._adversarial_configs = {}

        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )

        strategy_class = _build_benchmark_strategy()

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=strategy_class,
            default_strategy=strategy_class("light"),
            default_dataset_config=DatasetConfiguration(
                dataset_names=["harmbench"],
                max_dataset_size=8,
            ),
            scenario_result_id=scenario_result_id,
        )

    @staticmethod
    def _build_adversarial_configs(
        adversarial_models: list[PromptTarget],
    ) -> dict[str, AttackAdversarialConfig]:
        """
        Validate ``adversarial_models`` and wrap each into an ``AttackAdversarialConfig``.

        Returns:
            dict[str, AttackAdversarialConfig]: Adversarial configs keyed by inferred model label.

        Raises:
            ValueError: If the list is empty, not a list, or contains a target
                that does not satisfy :data:`CHAT_TARGET_REQUIREMENTS`.
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

        labeled_targets = AdversarialBenchmark._infer_labels(items=adversarial_models)
        return {label: AttackAdversarialConfig(target=target) for label, target in labeled_targets.items()}
>>>>>>> 4cf9ff5de64017ce09cbe9eeb653a35a9983cab4

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build atomic attacks from (technique × adversarial_target × dataset), then apply caching.

<<<<<<< HEAD
        Reads the user-supplied ``adversarial_targets`` parameter, resolves
        each name to a ``PromptTarget`` via ``TargetRegistry``, and
        cross-products the selected adversarial-capable techniques over the
        resolved targets and configured datasets. Each pair builds a
        non-registered per-pair factory via
        ``AttackTechniqueRegistry.build_factory_from_spec`` with
        ``adversarial_chat`` overridden to the resolved target — no global
        registry state is touched. When ``self._skip_cached`` is set, the
        final candidate list is then filtered against the live behavioral
        cache via ``_collect_cached_completion_pairs``, which delegates to
        ``pyrit.analytics.get_cached_results_for_technique`` for each
        unique ``(technique_eval_hash, objective_target_eval_hash)`` pair.
=======
        Factories are read from the singleton ``AttackTechniqueRegistry`` and
        narrowed to adversarial-capable ones. Each model is injected at
        create-time via ``attack_adversarial_config_override``.
>>>>>>> 4cf9ff5de64017ce09cbe9eeb653a35a9983cab4

        Returns:
            list[AtomicAttack]: The atomic attacks to actually execute on
            this run.

        Raises:
            ValueError: If the scenario has not been initialized, if
                ``adversarial_targets`` is missing/empty, or if any name in
                ``adversarial_targets`` is not registered.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

<<<<<<< HEAD
        target_names = self.params.get("adversarial_targets")
        if not target_names:
            raise ValueError(
                "AdversarialBenchmark requires at least one adversarial chat target. "
                "Pass --adversarial-targets <name> [<name> ...] on the CLI, or set "
                "scenario.args.adversarial_targets in .pyrit_conf. Use 'pyrit_scan list-targets' "
                "to see registered targets."
            )

        resolved_targets = self._resolve_adversarial_targets(target_names=target_names)
        # ``BenchmarkStrategy`` is built from adversarial-capable
        # ``SCENARIO_TECHNIQUES`` entries only (see ``_build_benchmark_strategy``),
        # so every selected strategy resolves to exactly one spec. Drift between the
        # enum and the catalog is silently ignored — the next strategy-class build
        # would surface it.
        specs_by_name = {spec.name: spec for spec in SCENARIO_TECHNIQUES}
        selected_specs = [specs_by_name[s.value] for s in self._scenario_strategies if s.value in specs_by_name]
=======
        if not self._adversarial_configs:
            raise ValueError(
                "AdversarialBenchmark requires adversarial_models to be passed at construction "
                "(non-empty list of chat-capable PromptTarget instances)."
            )

        benchmarkable_factories = AdversarialBenchmark._get_benchmarkable_factories()
        local_factories = {factory.name: factory for factory in benchmarkable_factories}
>>>>>>> 4cf9ff5de64017ce09cbe9eeb653a35a9983cab4

        scoring_config = AttackScoringConfig(objective_scorer=self._objective_scorer)
        seed_groups_by_dataset = self._dataset_config.get_seed_attack_groups()

        atomic_attacks: list[AtomicAttack] = []
        for spec in selected_specs:
            for target_name, target_instance in resolved_targets:
                pair_spec = dataclasses.replace(
                    spec,
                    adversarial_chat=target_instance,
                    adversarial_chat_key=None,
                )
                factory = AttackTechniqueRegistry.build_factory_from_spec(pair_spec)

                for dataset_name, seed_groups in seed_groups_by_dataset.items():
                    if factory.seed_technique is not None:
                        compatible_groups = SeedAttackGroup.filter_compatible(
                            seed_groups=seed_groups,
                            technique=factory.seed_technique,
                        )
                        skipped = len(seed_groups) - len(compatible_groups)
                        if skipped:
                            logger.info(
                                f"Skipped {skipped} seed group(s) from '{dataset_name}' for technique "
                                f"'{spec.name}' (prompt sequences overlap with simulated conversation)."
                            )
                        if not compatible_groups:
                            logger.warning(
                                f"No compatible seed groups in '{dataset_name}' for technique "
                                f"'{spec.name}', skipping this (technique, target, dataset) triple."
                            )
                            continue
                    else:
                        compatible_groups = list(seed_groups)

                    attack_technique = factory.create(
                        objective_target=self._objective_target,
                        attack_scoring_config=scoring_config,
                    )
                    # ``display_group`` is set explicitly here so result roll-ups group by the
                    # TargetRegistry name the caller passed via ``--adversarial-targets`` —
                    # not by any internal field on the PromptTarget instance (e.g. ``_model_name``).
                    # Because we override ``_get_atomic_attacks_async`` entirely, the base
                    # ``Scenario._build_display_group`` hook is never consulted; ``Scenario._finalize``
                    # then reads ``aa.display_group`` directly (scenario.py:721).
                    atomic_attacks.append(
                        AtomicAttack(
                            atomic_attack_name=f"{spec.name}__{target_name}_{dataset_name}",
                            attack_technique=attack_technique,
                            seed_groups=list(compatible_groups),
                            adversarial_chat=target_instance,
                            objective_scorer=self._objective_scorer,
                            memory_labels=self._memory_labels,
                            display_group=target_name,
                        )
                    )

        if not self._skip_cached:
            return atomic_attacks

        cached_technique_hashes = self._collect_cached_completion_pairs(atomic_attacks=atomic_attacks)
        filtered = [c for c in atomic_attacks if c.technique_eval_hash not in cached_technique_hashes]
        skipped = len(atomic_attacks) - len(filtered)
        if skipped > 0:
            logger.info(
                "skip_cached=True: dropping %d/%d atomic attack(s) already completed for the "
                "current objective target (matched by technique_eval_hash × objective_target_eval_hash).",
                skipped,
                len(atomic_attacks),
            )
        return filtered

    def _resolve_adversarial_targets(self, *, target_names: list[str]) -> list[tuple[str, PromptTarget]]:
        """
        Resolve each requested adversarial target name to its registered instance.

        Args:
            target_names: Names supplied via the ``adversarial_targets``
                parameter.

        Returns:
            list[tuple[str, PromptTarget]]: ``(registry_name, instance)``
            pairs in the order requested.

        Raises:
            ValueError: If any name is not registered. The error lists both
                the missing names and the names that are available, so
                typos fail loudly.
        """
        target_registry = TargetRegistry.get_registry_singleton()
        resolved: list[tuple[str, PromptTarget]] = []
        unknown: list[str] = []
        for name in target_names:
            instance = target_registry.get_instance_by_name(name)
            if instance is None:
                unknown.append(name)
            else:
                resolved.append((name, instance))

        if unknown:
            available = sorted(target_registry.get_names())
            raise ValueError(
                f"AdversarialBenchmark: adversarial_targets {sorted(unknown)} not found in TargetRegistry. "
                f"Available targets: {available}."
            )

        return resolved

<<<<<<< HEAD
    def _collect_cached_completion_pairs(self, *, atomic_attacks: list[AtomicAttack]) -> set[str]:
        """
        Return the set of ``technique_eval_hash`` values already cached for this scenario's objective target.

        Delegates to ``pyrit.analytics.get_cached_results_for_technique`` for
        each unique technique hash among ``atomic_attacks``. A technique is
        considered cached when the analytics helper returns at least one
        ``AttackResult`` with outcome ``SUCCESS`` or ``FAILURE`` for the
        ``(technique_eval_hash × objective_target_eval_hash)`` pair —
        ``ERROR`` and ``UNDETERMINED`` outcomes are ignored so transient
        failures retry on the next run.

        The objective-target eval hash is computed once from
        ``self._objective_target_identifier`` (populated by the base
        ``Scenario.initialize_async``) via
        ``ObjectiveTargetEvaluationIdentifier``. The cache is intentionally
        scenario-agnostic: any prior run that produced a matching (technique
        × objective target) result counts as a hit, regardless of scenario
        name or ``VERSION``.

        Args:
            atomic_attacks: The candidate atomic attacks built earlier in
                ``_get_atomic_attacks_async``. Only their
                ``technique_eval_hash`` values are read.

        Returns:
            set[str]: ``technique_eval_hash`` values that have at least one
            qualifying cached ``AttackResult``. Empty set when the scenario
            has no objective target identifier or every analytics lookup
            fails (logged at warning level) — caching becomes a no-op rather
            than blocking the run.
        """
        cached_hashes: set[str] = set()

        if self._objective_target_identifier is None:
            return cached_hashes

        try:
            objective_target_eval_hash = ObjectiveTargetEvaluationIdentifier(
                self._objective_target_identifier
            ).eval_hash
        except Exception as exc:
            logger.warning(
                "skip_cached: failed to compute objective_target eval hash (%s); skipping cache filter.",
                exc,
            )
            return cached_hashes

        unique_technique_hashes = {c.technique_eval_hash for c in atomic_attacks if c.technique_eval_hash}

        for technique_eval_hash in unique_technique_hashes:
            try:
                matches = get_cached_results_for_technique(
                    self._memory,
                    technique_eval_hash=technique_eval_hash,
                    objective_target_eval_hash=objective_target_eval_hash,
                )
            except Exception as exc:
                logger.warning(
                    "skip_cached: analytics lookup failed for technique_eval_hash=%s (%s); not treating it as cached.",
                    technique_eval_hash,
                    exc,
                )
                continue
            if any(m.outcome in (AttackOutcome.SUCCESS, AttackOutcome.FAILURE) for m in matches):
                cached_hashes.add(technique_eval_hash)

        return cached_hashes
=======
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


@cache
def _build_benchmark_strategy() -> type[ScenarioStrategy]:
    """
    Module-level cached builder so all callers share the same strategy enum class.

    Returns:
        type[ScenarioStrategy]: The dynamically generated BenchmarkStrategy enum class.
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
>>>>>>> 4cf9ff5de64017ce09cbe9eeb653a35a9983cab4
