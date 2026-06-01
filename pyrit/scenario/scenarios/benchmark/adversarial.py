# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AdversarialBenchmark scenario — compare attack success rate across adversarial models."""

from __future__ import annotations

import logging
from functools import cache
from typing import TYPE_CHECKING, ClassVar

from pyrit.analytics import get_cached_results_for_technique
from pyrit.common import Parameter, apply_defaults
from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig
from pyrit.models import ObjectiveTargetEvaluationIdentifier
from pyrit.models import AttackOutcome, SeedAttackGroup
from pyrit.registry import AttackTechniqueRegistry, TargetRegistry
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


logger = logging.getLogger(__name__)


@cache
def _build_benchmark_strategy() -> type[ScenarioStrategy]:
    """
    Build the ``BenchmarkStrategy`` enum from the registered factory catalog.

    Reads ``core`` adversarial-capable factories from the
    ``AttackTechniqueRegistry`` singleton and passes them to
    ``build_strategy_class_from_factories``. The resulting enum has one
    concrete member per factory (e.g. ``red_teaming``, ``tap``,
    ``crescendo_simulated``) plus ``default`` / ``light`` / ``single_turn``
    / ``multi_turn`` aggregates derived from each factory's ``strategy_tags``.

    The (technique × target) cross-product is materialized lazily in
    ``AdversarialBenchmark._get_atomic_attacks_async`` from the
    user-supplied ``adversarial_targets`` parameter.

    Returns:
        type[ScenarioStrategy]: The dynamically generated ``BenchmarkStrategy`` class.
    """
    registry = AttackTechniqueRegistry.get_registry_singleton()
    factories = [
        factory
        for factory in registry.get_factories_or_raise().values()
        if factory.uses_adversarial and "core" in factory.strategy_tags
    ]
    return AttackTechniqueRegistry.build_strategy_class_from_factories(  # type: ignore[ty:invalid-return-type]
        class_name="BenchmarkStrategy",
        factories=factories,
        aggregate_tags={
            "default": TagQuery.any_of("default"),
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
    selected adversarial-capable ``core`` factory in the
    ``AttackTechniqueRegistry`` and each requested target, it calls
    ``factory.create(attack_adversarial_config_override=...)`` with the
    resolved target — no global registry mutation. The resulting
    ``AtomicAttack`` is named ``f"{technique}__{target}_{dataset}"`` with
    ``display_group`` set to the target's registry name so per-model ASR
    rolls up naturally in result displays.
    """

    #: Bumped from 1 → 2 by the refactor that moved adversarial targets
    #: from a constructor parameter to the ``adversarial_targets`` scenario
    #: parameter and changed ``atomic_attack_name`` from
    #: ``{technique}__{model}__{dataset}`` to ``{technique}__{target}_{dataset}``.
    #: ``use_cached`` only matches against prior runs at the current
    #: ``VERSION``; v1 results remain queryable but won't suppress v2 runs.
    VERSION: int = 2

    #: AdversarialBenchmark compares attack-success rates across adversarial models; a baseline
    #: attack would be model-independent and contribute no signal to the comparison.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

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

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        use_cached: bool = False,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the AdversarialBenchmark scenario.

        Args:
            objective_scorer: ``TrueFalseScorer`` used to evaluate attack
                success. Defaults to the registered default objective
                scorer (typically the composite refusal+scale scorer set
                up by an initializer). Widening to general ``Scorer``
                support (covering ``FloatScaleScorer``, etc.) is tracked
                as a follow-up.
            use_cached: When ``True``, ``_get_atomic_attacks_async`` filters
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
        self._use_cached: bool = use_cached

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

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build atomic attacks from (technique × adversarial_target × dataset), then apply caching.

        Reads the user-supplied ``adversarial_targets`` parameter, resolves
        each name to a ``PromptTarget`` via ``TargetRegistry``, and
        cross-products the selected adversarial-capable techniques over the
        resolved targets and configured datasets. Each pair calls
        ``factory.create(attack_adversarial_config_override=...)`` with the
        resolved target — no global registry state is touched. When
        ``self._use_cached`` is set, the final candidate list is filtered
        against the live behavioral cache via
        ``_collect_cached_completion_pairs``, which delegates to
        ``pyrit.analytics.get_cached_results_for_technique`` for each
        unique ``(technique_eval_hash, objective_target_eval_hash)`` pair.

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

        target_names = self.params.get("adversarial_targets")
        if not target_names:
            raise ValueError(
                "AdversarialBenchmark requires at least one adversarial chat target. "
                "Pass --adversarial-targets <name> [<name> ...] on the CLI, or set "
                "scenario.args.adversarial_targets in .pyrit_conf. Use 'pyrit_scan list-targets' "
                "to see registered targets."
            )

        resolved_targets = self._resolve_adversarial_targets(target_names=target_names)
        all_factories = AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise()
        selected_factories = [
            all_factories[s.value] for s in self._scenario_strategies if s.value in all_factories
        ]

        scoring_config = AttackScoringConfig(objective_scorer=self._objective_scorer)
        seed_groups_by_dataset = self._dataset_config.get_seed_attack_groups()

        atomic_attacks: list[AtomicAttack] = []
        for factory in selected_factories:
            for target_name, target_instance in resolved_targets:
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
                                f"'{factory.name}' (prompt sequences overlap with simulated conversation)."
                            )
                        if not compatible_groups:
                            logger.warning(
                                f"No compatible seed groups in '{dataset_name}' for technique "
                                f"'{factory.name}', skipping this (technique, target, dataset) triple."
                            )
                            continue
                    else:
                        compatible_groups = list(seed_groups)

                    attack_technique = factory.create(
                        objective_target=self._objective_target,
                        attack_scoring_config=scoring_config,
                        attack_adversarial_config_override=AttackAdversarialConfig(target=target_instance),
                    )
                    # ``display_group`` is set explicitly here so result roll-ups group by the
                    # TargetRegistry name the caller passed via ``--adversarial-targets`` —
                    # not by any internal field on the PromptTarget instance (e.g. ``_model_name``).
                    # Because we override ``_get_atomic_attacks_async`` entirely, the base
                    # ``Scenario._build_display_group`` hook is never consulted; ``Scenario._finalize``
                    # then reads ``aa.display_group`` directly (scenario.py:721).
                    atomic_attacks.append(
                        AtomicAttack(
                            atomic_attack_name=f"{factory.name}__{target_name}_{dataset_name}",
                            attack_technique=attack_technique,
                            seed_groups=list(compatible_groups),
                            adversarial_chat=target_instance,
                            objective_scorer=self._objective_scorer,
                            memory_labels=self._memory_labels,
                            display_group=target_name,
                        )
                    )

        if not self._use_cached:
            return atomic_attacks

        cached_technique_hashes = self._collect_cached_completion_pairs(atomic_attacks=atomic_attacks)
        filtered = [c for c in atomic_attacks if c.technique_eval_hash not in cached_technique_hashes]
        skipped = len(atomic_attacks) - len(filtered)
        if skipped > 0:
            logger.info(
                "use_cached=True: skipping %d/%d atomic attack(s) already completed for the "
                "current objective target (matched by technique_eval_hash × objective_target_eval_hash).",
                skipped,
                len(atomic_attacks),
            )
        # TODO: inject prior AttackResult rows for the skipped attacks into the current ScenarioResult
        # so use_cached=True produces a complete result rather than a partial one.
        # The skipped attacks' names are: [a.atomic_attack_name for a in atomic_attacks if a not in filtered]
        # Fetch their results via get_cached_results_for_technique and add them as pre-populated slots
        # in ScenarioResult.attack_results (requires overriding initialize_async or a post-populate hook).
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
