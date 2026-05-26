# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AdversarialBenchmark scenario — compare attack success rate across adversarial models."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.models import AttackOutcome
from pyrit.registry import AttackTechniqueRegistry, AttackTechniqueSpec
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario

if TYPE_CHECKING:
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
    from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


logger = logging.getLogger(__name__)


#: Strategy tag applied by ``BenchmarkInitializer`` to every fanned variant it
#: registers in ``AttackTechniqueRegistry``. The benchmark scenario reads its
#: strategy enum from entries carrying this tag.
BENCHMARK_FANOUT_TAG: str = "benchmark_fanout"


class _StrategyOnlyMarker:
    """
    Sentinel attack class used only to satisfy ``AttackTechniqueSpec.attack_class``
    when reconstructing minimal specs for strategy-enum construction.

    ``AttackTechniqueRegistry.build_strategy_class_from_specs`` reads only
    ``spec.name`` and ``spec.strategy_tags`` — never ``attack_class`` — so the
    sentinel is safe. At attack-execution time the base
    ``Scenario._get_atomic_attacks_async`` looks up the real factory by name
    from ``AttackTechniqueRegistry`` (where ``BenchmarkInitializer`` registered
    it), so this sentinel never reaches a runtime construction site.
    """


def _build_benchmark_strategy() -> type[ScenarioStrategy]:
    """
    Build the ``BenchmarkStrategy`` enum from ``BenchmarkInitializer``-registered fanout.

    *Fanned entries* (also called *fanned variants*) are the per-target copies
    of adversarial-capable scenario techniques that ``BenchmarkInitializer``
    registers into ``AttackTechniqueRegistry``. For each adversarial-capable
    technique in ``SCENARIO_TECHNIQUES`` and each adversarial-tagged target
    in ``TargetRegistry``, the initializer creates one fanned variant named
    ``f"{source_technique}__{target_name}"`` with the live target bound onto
    ``adversarial_chat`` and the strategy tag
    :data:`BENCHMARK_FANOUT_TAG` appended. This function reads those entries
    back and builds an enum whose concrete members are exactly the fanned
    variants.

    Implementation note: this is a module-level function rather than a
    ``@staticmethod`` on ``AdversarialBenchmark``. Strategy-class
    construction never reads scenario instance state, so the function does
    not belong to the class; module-level placement makes the dependency
    (only the registry) explicit and the unit-test surface flat.

    Reconstructs minimal ``AttackTechniqueSpec`` stand-ins (name +
    strategy_tags only) from each fanned entry to pass into
    ``build_strategy_class_from_specs``. The sentinel
    :class:`_StrategyOnlyMarker` is used for the required ``attack_class``
    field — see the sentinel's docstring for why this is safe.

    Aggregate selectors on the generated enum:

    * ``all`` — every fanned variant (auto-included by the builder).
    * ``light`` — variants tagged ``"light"`` (inherited from the source spec).
    * ``single_turn`` / ``multi_turn`` — variants tagged with the matching
      turn-style tag inherited from the source spec.

    Per-target selection is also available via the auto-applied
    ``f"model:{target_name}"`` tag on each fanned variant, accessible by name
    on the generated enum (e.g.
    ``BenchmarkStrategy("red_teaming__adversarial_chat_singleturn")``).

    Returns:
        type[ScenarioStrategy]: The dynamically generated ``BenchmarkStrategy`` class.
    """
    registry = AttackTechniqueRegistry.get_registry_singleton()
    fanned_entries = registry.get_by_tag(tag=BENCHMARK_FANOUT_TAG)

    fanned_specs = [
        AttackTechniqueSpec(
            name=entry.name,
            attack_class=_StrategyOnlyMarker,
            strategy_tags=list(entry.tags.keys()),
        )
        for entry in fanned_entries
    ]

    return AttackTechniqueRegistry.build_strategy_class_from_specs(  # type: ignore[ty:invalid-return-type]
        class_name="BenchmarkStrategy",
        specs=fanned_specs,
        aggregate_tags={
            "light": TagQuery.any_of("light"),
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
    )


class AdversarialBenchmark(Scenario):
    """
    Benchmark scenario that compares the attack success rate (ASR) across adversarial models.

    Adversarial-model fan-out is provided by ``BenchmarkInitializer``, which
    registers per-target *fanned variants* of adversarial-capable scenario
    techniques into ``AttackTechniqueRegistry`` tagged ``benchmark_fanout``.
    This scenario reads those variants and builds its strategy enum from
    them, so the set of available strategies reflects whichever adversarial
    targets were discovered when ``BenchmarkInitializer`` ran (typically via
    ``.pyrit_conf`` initializer ordering).

    Inherits the base ``Scenario._get_atomic_attacks_async`` loop with no
    override; the fanned ``adversarial_chat`` binding lives on the
    registered factories, so atomic-attack construction needs no special
    handling here.

    When permuted atomic attacks materialize
    =========================================
    The (technique × target × dataset) cross-product now happens in two
    stages, not one (the pre-collapse override did all three at runtime):

    1. **Initializer time** — ``BenchmarkInitializer.initialize_async``
       runs the (technique × adversarial-target) cross-product. For each
       adversarial-capable technique in ``SCENARIO_TECHNIQUES`` and each
       adversarial-tagged target in ``TargetRegistry``, it registers one
       fanned ``AttackTechniqueFactory`` into ``AttackTechniqueRegistry``
       with the live target baked onto the factory's adversarial config.
       After this step, the registry contains N×M fanned entries where N
       is the count of adversarial-capable techniques and M is the count of
       discovered adversarial targets.

    2. **Scenario runtime** — ``Scenario._get_atomic_attacks_async``
       (inherited, base class) runs the (fanned-variant × dataset)
       cross-product. It iterates ``self._scenario_strategies`` (the
       fanned-variant names the user picked via the ``BenchmarkStrategy``
       enum), pairs each with every seed group in
       ``self._dataset_config``, and builds one ``AtomicAttack`` per pair.
       The target binding rides through on the factory created in step 1,
       so no per-target handling is needed at this layer.

    The user-observable result is the same shape as before
    (one ``AtomicAttack`` per (technique, target, dataset) triple), but the
    target dimension is now owned by the initializer and the dataset
    dimension is owned by the scenario.

    Display grouping is by target name (the part after ``__`` in each
    fanned technique name) rather than by technique, so per-model ASR rolls
    up naturally in result displays.
    """

    #: Bumped from 1 (pre-collapse) to 2 because the ``atomic_attack_name``
    #: format changed from ``f"{technique}__{model}__{dataset}"`` (triple-segment,
    #: old override-driven) to ``f"{technique}__{model}_{dataset}"`` (double-
    #: underscore-then-single-underscore, base-inherited). Cached results from
    #: VERSION=1 remain queryable but won't suppress fresh runs.
    VERSION: int = 2

    #: AdversarialBenchmark compares attack-success rates across adversarial models; a baseline
    #: attack would be model-independent and contribute no signal to the comparison.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    @classmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Return the ``BenchmarkStrategy`` enum, building on first access.

        The enum is cached per-class for the lifetime of the process. To
        rebuild after registry mutations (e.g. after re-running
        ``BenchmarkInitializer`` with different adversarial targets), set
        ``AdversarialBenchmark._cached_strategy_class = None`` and call again.

        Returns:
            type[ScenarioStrategy]: The ``BenchmarkStrategy`` enum class.
        """
        if cls._cached_strategy_class is None:
            cls._cached_strategy_class = _build_benchmark_strategy()
        return cls._cached_strategy_class

    @classmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Return the default strategy (``light``).

        Returns:
            ScenarioStrategy: The ``light`` aggregate member — runs the subset
            of benchmark-friendly techniques that finish quickly with modest
            system resources.
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

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        skip_cached: bool = False,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the AdversarialBenchmark scenario.

        Args:
            objective_scorer: Scorer for evaluating attack success. The
                annotation is the broad ``Scorer`` base class for forward
                compatibility with the planned non-``TrueFalseScorer``
                scoring follow-up (see PR description / follow-up issue
                tracker), but the runtime contract is currently still
                ``TrueFalseScorer``: any other ``Scorer`` subclass raises
                ``TypeError`` at construction with a message pointing at
                the follow-up. Defaults to the registered default objective
                scorer (typically the composite refusal+scale scorer set
                up by an initializer), which is always a
                ``TrueFalseScorer``.
            skip_cached: When ``True``, ``_get_atomic_attacks_async`` filters
                out atomic attacks whose ``(atomic_attack_name,
                technique_eval_hash)`` tuple already appears in a prior
                ``COMPLETED`` ``ScenarioResult`` for the same scenario name
                and version with outcome ``SUCCESS`` or ``FAILURE``.
                ``ERROR`` and ``UNDETERMINED`` outcomes always retry. Cache
                identity is content-derived via
                ``AtomicAttack.technique_eval_hash``, so two atomic attacks
                with the same name but different technique configurations
                (e.g. different scorer) do not cross-pollinate.
            scenario_result_id: Optional ID of an existing scenario result
                to resume.

        Raises:
            TypeError: If ``objective_scorer`` is a ``Scorer`` subclass
                other than ``TrueFalseScorer`` (full non-true/false support
                is tracked as a follow-up; the type annotation is widened
                ahead of the runtime support).
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

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build the base set of atomic attacks, then filter out cached completions when requested.

        Delegates to the base ``Scenario._get_atomic_attacks_async`` to
        construct the (fanned-variant × dataset) candidate list, then drops
        any candidate whose ``(atomic_attack_name, technique_eval_hash)``
        tuple appears in :meth:`_collect_cached_completion_pairs` (only when
        ``self._skip_cached`` is ``True``). Always returns the unfiltered
        base list when caching is disabled.

        Returns:
            list[AtomicAttack]: The atomic attacks to actually execute on
            this run.
        """
        candidates = await super()._get_atomic_attacks_async()
        if not self._skip_cached:
            return candidates

        cached_pairs = self._collect_cached_completion_pairs()
        filtered = [c for c in candidates if (c.atomic_attack_name, c.technique_eval_hash) not in cached_pairs]
        skipped = len(candidates) - len(filtered)
        if skipped > 0:
            logger.info(
                "skip_cached=True: dropping %d/%d atomic attack(s) already completed in prior runs.",
                skipped,
                len(candidates),
            )
        return filtered

    def _collect_cached_completion_pairs(self) -> set[tuple[str, str | None]]:
        """
        Collect cache keys for atomic attacks that completed in any prior run of this scenario.

        Walks ``ScenarioResult`` rows for the same scenario name and
        ``VERSION``, restricts to ``scenario_run_state == "COMPLETED"``,
        then walks the linked ``AttackResult`` rows (joined via
        ``AttackResultEntry.attribution_parent_id``) and records the
        ``(atomic_attack_name, parent_eval_hash)`` tuple for every
        ``SUCCESS`` or ``FAILURE`` outcome. The pair shape mirrors the
        ``(atomic_attack_name, technique_eval_hash)`` tuple used by
        :meth:`_get_atomic_attacks_async` so a direct ``in`` check filters
        candidates without further key construction.

        Resilient to attribution-data variation: rows whose
        ``attribution_data`` is ``None`` or missing ``parent_collection``
        are skipped. Rows without ``parent_eval_hash`` enter the cache with
        ``None`` in that slot, so they only match candidates whose
        ``technique_eval_hash`` also resolves to ``None`` (currently never,
        since ``AtomicAttack.technique_eval_hash`` is always populated post-#1758).

        Returns:
            set[tuple[str, str | None]]: Cache keys for already-completed
            atomic attacks. Empty set on any unexpected error (logged at
            warning level) — caching becomes a no-op rather than blocking
            the run.
        """
        scenario_name = type(self).__name__
        cached_pairs: set[tuple[str, str | None]] = set()

        try:
            prior_results = self._memory.get_scenario_results(
                scenario_name=scenario_name,
                scenario_version=self.VERSION,
            )
        except Exception as exc:
            logger.warning("skip_cached: failed to query prior scenario results (%s); skipping cache filter.", exc)
            return cached_pairs

        for scenario_result in prior_results:
            if scenario_result.scenario_run_state != "COMPLETED":
                continue
            if scenario_result.id is None:
                continue
            try:
                attack_results = self._memory.get_attack_results(scenario_result_id=str(scenario_result.id))
            except Exception as exc:
                logger.warning(
                    "skip_cached: failed to load attack results for scenario %s (%s); skipping that run.",
                    scenario_result.id,
                    exc,
                )
                continue

            for ar in attack_results:
                if ar.outcome not in (AttackOutcome.SUCCESS, AttackOutcome.FAILURE):
                    continue
                data = ar.attribution_data or {}
                atomic_attack_name = data.get("parent_collection")
                if not atomic_attack_name:
                    continue
                parent_eval_hash = data.get("parent_eval_hash")
                cached_pairs.add((atomic_attack_name, parent_eval_hash))

        return cached_pairs

    def _build_display_group(self, *, technique_name: str, seed_group_name: str) -> str:
        """
        Group atomic-attack results by adversarial-target label rather than by technique.

        Fanned technique names have the format ``f"{source}__{target_name}"``
        (per ``BenchmarkInitializer``), so the target label is everything
        after the ``__`` separator. Falls back to the full technique name
        when no separator is present so legacy / non-fanned strategies still
        render with a sensible label.

        Args:
            technique_name: The fanned technique name, e.g.
                ``"red_teaming__adversarial_chat_singleturn"``.
            seed_group_name: Unused for this scenario (display rolls up
                per-target, not per-seed-group).

        Returns:
            str: The display group label — the target portion of the fanned
            name when ``__`` is present, otherwise the full technique name.
        """
        return technique_name.split("__", 1)[1] if "__" in technique_name else technique_name
