# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AdversarialBenchmark scenario — compare attack success rate across adversarial models."""

from __future__ import annotations

import logging
from functools import cache
from typing import TYPE_CHECKING, ClassVar

from pyrit.analytics import get_cached_results_for_technique
from pyrit.common import apply_defaults
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ObjectiveTargetEvaluationIdentifier,
    ScenarioResult,
)
from pyrit.models.parameter import Parameter
from pyrit.registry import AttackTechniqueRegistry, TargetRegistry
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import (
    MatrixAtomicAttackBuilder,
    resolve_technique_factories,
)
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


logger = logging.getLogger(__name__)


@cache
def _build_benchmark_strategy() -> type[ScenarioStrategy]:
    """
    Build the ``BenchmarkStrategy`` enum from the registered factory catalog.

    Reads ``core`` adversarial-capable factories from the
    ``AttackTechniqueRegistry`` singleton and passes them to
    ``build_strategy_class_from_factories``. Factories that bake their own
    ``adversarial_chat`` are excluded — the benchmark sweeps each technique
    across the user-supplied targets, which is incompatible with a technique
    that pins its own adversarial target. The resulting enum has one
    concrete member per factory (e.g. ``red_teaming``, ``tap``,
    ``crescendo_simulated``) plus ``default`` / ``light`` / ``single_turn``
    / ``multi_turn`` aggregates derived from each factory's ``strategy_tags``.

    The (technique × target) cross-product is materialized lazily in
    ``AdversarialBenchmark._build_atomic_attacks_async`` from the
    user-supplied ``adversarial_targets`` parameter.

    Returns:
        type[ScenarioStrategy]: The dynamically generated ``BenchmarkStrategy`` class.
    """
    registry = AttackTechniqueRegistry.get_registry_singleton()
    factories = [
        factory
        for factory in registry.get_factories_or_raise().values()
        if factory.uses_adversarial and "core" in factory.strategy_tags and factory.adversarial_chat is None
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
    programmatically via ``TargetRegistry.get_registry_singleton().instances.register``.

    At run time, ``_build_atomic_attacks_async`` performs the
    ``(technique × adversarial_target × dataset)`` cross-product: for each
    selected adversarial-capable ``core`` factory in the
    ``AttackTechniqueRegistry`` and each requested target, it calls
    ``factory.create(adversarial_chat=...)`` with the
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
        ``_build_atomic_attacks_async`` raises ``ValueError`` if
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
                    "(via TargetInitializer or TargetRegistry instance registration). "
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
            use_cached: When ``True``, ``_build_atomic_attacks_async`` filters
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
        self._precomputed_cached_results: dict[str, list[AttackResult]] = {}
        self._precomputed_cached_display_groups: dict[str, str] = {}
        self._cached_results_by_name: dict[str, list[AttackResult]] = {}

        strategy_class = _build_benchmark_strategy()

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            strategy_class=strategy_class,
            default_strategy=strategy_class("light"),
            default_dataset_config=DatasetAttackConfiguration(
                dataset_names=["harmbench"],
                max_dataset_size=8,
            ),
            scenario_result_id=scenario_result_id,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build atomic attacks from (technique × adversarial_target × dataset), then apply caching.

        Reads the user-supplied ``adversarial_targets`` parameter, resolves each name to a
        ``PromptTarget`` via ``TargetRegistry``, and delegates the
        ``(technique × target × dataset)`` cross-product to ``MatrixAtomicAttackBuilder``
        with the resolved targets as its adversarial-target axis. Each pair calls
        ``factory.create(adversarial_chat=...)`` with the resolved target — no global
        registry state is touched. When ``self._use_cached`` is set, the resulting candidate
        list is filtered against the live behavioral cache via
        ``_collect_cached_completion_pairs``, which delegates to
        ``pyrit.analytics.get_cached_results_for_technique`` for each unique
        ``(technique_eval_hash, objective_target_eval_hash)`` pair.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The atomic attacks to actually execute on this run.

        Raises:
            ValueError: If ``adversarial_targets`` is missing/empty, or if any name in
                ``adversarial_targets`` is not registered.
        """
        target_names = self.params.get("adversarial_targets")
        if not target_names:
            raise ValueError(
                "AdversarialBenchmark requires at least one adversarial chat target. "
                "Pass --adversarial-targets <name> [<name> ...] on the CLI, or set "
                "scenario.args.adversarial_targets in .pyrit_conf. Use 'pyrit_scan list-targets' "
                "to see registered targets."
            )

        resolved_targets = self._resolve_adversarial_targets(target_names=target_names)
        technique_factories = resolve_technique_factories(context=context)

        builder = MatrixAtomicAttackBuilder(
            objective_target=context.objective_target,
            objective_scorer=self._objective_scorer,
            memory_labels=context.memory_labels,
        )
        # ``display_group`` is the TargetRegistry name the caller passed via
        # ``--adversarial-targets`` so per-model ASR rolls up naturally — not any internal
        # field on the PromptTarget instance (e.g. ``_model_name``). The builder's default
        # ``{technique}__{target}_{dataset}`` naming preserves the VERSION=2 cache key shape.
        atomic_attacks = builder.build(
            technique_factories=technique_factories,
            dataset_groups=context.seed_groups_by_dataset,
            adversarial_targets=resolved_targets,
            display_group_fn=lambda combo: combo.target_name or "",
            include_baseline=False,
        )

        if not self._use_cached:
            return atomic_attacks

        cached_attack_names = self._collect_cached_completion_pairs(atomic_attacks=atomic_attacks)
        filtered = [c for c in atomic_attacks if c.atomic_attack_name not in cached_attack_names]
        skipped_attacks = [c for c in atomic_attacks if c.atomic_attack_name in cached_attack_names]
        if skipped_attacks:
            logger.info(
                "use_cached=True: skipping %d/%d atomic attack(s) already completed for the "
                'current objective target (dataset-scoped via attribution_data["parent_collection"]).',
                len(skipped_attacks),
                len(atomic_attacks),
            )
            # Pre-populate prior results for skipped attacks so run_async can surface them in
            # ScenarioResult.attack_results. _cached_results_by_name already holds the
            # attribution-filtered list keyed by atomic_attack_name, so no further filtering needed.
            self._precomputed_cached_results = {}
            self._precomputed_cached_display_groups = {}
            for attack in skipped_attacks:
                self._precomputed_cached_results[attack.atomic_attack_name] = self._cached_results_by_name.get(
                    attack.atomic_attack_name, []
                )
                self._precomputed_cached_display_groups[attack.atomic_attack_name] = attack.display_group
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
            instance = target_registry.instances.get(name)
            if instance is None:
                unknown.append(name)
            else:
                resolved.append((name, instance))

        if unknown:
            available = sorted(target_registry.instances.get_names())
            raise ValueError(
                f"AdversarialBenchmark: adversarial_targets {sorted(unknown)} not found in TargetRegistry. "
                f"Available targets: {available}."
            )

        return resolved

    async def run_async(self) -> ScenarioResult:
        """
        Run the scenario and merge any precomputed cached results into the returned ``ScenarioResult``.

        When ``use_cached=True`` skipped atomic attacks whose prior results were
        loaded during ``_build_atomic_attacks_async``, this override attaches
        those results (and their display-group labels) to the live scenario
        result so the final report reflects both newly-executed and
        cache-served runs.

        Returns:
            ScenarioResult: The scenario result with cached attack results merged
            into ``attack_results`` and cached display groups merged into
            ``display_group_map``.
        """
        result = await super().run_async()
        if self._precomputed_cached_results:
            for attack_name, prior_results in self._precomputed_cached_results.items():
                result.attack_results.setdefault(attack_name, []).extend(prior_results)
            result.display_group_map.update(self._precomputed_cached_display_groups)
        return result

    def _collect_cached_completion_pairs(self, *, atomic_attacks: list[AtomicAttack]) -> set[str]:
        """
        Return the set of ``atomic_attack_name`` values already cached for this scenario's objective target.

        Database queries are deduplicated by unique ``technique_eval_hash`` (one query per hash,
        regardless of how many atomic attacks share that hash), then the skip eligibility
        decision is applied per-atomic-attack using a Python-side filter on
        ``attribution_data["parent_collection"]``.

        **Dataset-level scoping is implemented as a semantic Python filter, not a database query.**
        ``get_cached_results_for_technique`` has no ``dataset`` parameter; it returns all results
        for a given ``(technique_eval_hash × objective_target_eval_hash)`` pair regardless of which
        dataset they came from. The scoping happens here: a retrieved result only counts toward the
        skip decision for atomic-attack *X* if its ``attribution_data["parent_collection"]`` equals
        ``X.atomic_attack_name``. This means two atomic attacks that share a technique+target hash
        (e.g. the same red-teaming technique run against the same model for both ``harmbench`` and
        ``advbench``) are cached independently: a harmbench result will never cause the advbench
        slot to be skipped.

        A dataset slot is considered cached when the attribution-filtered result set contains at
        least one ``AttackResult`` with outcome ``SUCCESS`` or ``FAILURE`` —
        ``ERROR`` and ``UNDETERMINED`` outcomes are ignored so transient failures retry on the
        next run.

        The objective-target eval hash is computed once from
        ``self._objective_target_identifier`` (populated by the base
        ``Scenario.initialize_async``) via
        ``ObjectiveTargetEvaluationIdentifier``.

        As a side effect, populates ``self._cached_results_by_name`` with the
        attribution-filtered ``AttackResult`` lists keyed by ``atomic_attack_name`` so that
        ``_build_atomic_attacks_async`` can inject them into the final ``ScenarioResult``
        via ``run_async`` without re-filtering.

        Args:
            atomic_attacks: The candidate atomic attacks built earlier in
                ``_build_atomic_attacks_async``.

        Returns:
            set[str]: ``atomic_attack_name`` values that have at least one qualifying cached
            ``AttackResult``. Empty set when the scenario has no objective target identifier
            or every analytics lookup fails (logged at warning level) — caching becomes a
            no-op rather than blocking the run.
        """
        cached_names: set[str] = set()
        self._cached_results_by_name: dict[str, list[AttackResult]] = {}

        if self._objective_target_identifier is None:
            return cached_names

        try:
            objective_target_eval_hash = ObjectiveTargetEvaluationIdentifier(
                self._objective_target_identifier
            ).eval_hash
        except Exception as exc:
            logger.warning(
                "skip_cached: failed to compute objective_target eval hash (%s); skipping cache filter.",
                exc,
            )
            return cached_names

        unique_technique_hashes = {c.technique_eval_hash for c in atomic_attacks if c.technique_eval_hash}

        # One DB query per unique hash (deduplication), results stored temporarily by hash.
        raw_results_by_hash: dict[str, list[AttackResult]] = {}
        for technique_eval_hash in unique_technique_hashes:
            try:
                raw_results_by_hash[technique_eval_hash] = get_cached_results_for_technique(
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

        # Per-attack attribution filter: only count results that were produced for this
        # specific atomic_attack_name slot (dataset-level scoping via parent_collection).
        for attack in atomic_attacks:
            if not attack.technique_eval_hash or attack.technique_eval_hash not in raw_results_by_hash:
                continue
            attributed = [
                r
                for r in raw_results_by_hash[attack.technique_eval_hash]
                if r.attribution_data and r.attribution_data.get("parent_collection") == attack.atomic_attack_name
            ]
            if any(r.outcome in (AttackOutcome.SUCCESS, AttackOutcome.FAILURE) for r in attributed):
                cached_names.add(attack.atomic_attack_name)
                self._cached_results_by_name[attack.atomic_attack_name] = attributed

        return cached_names
