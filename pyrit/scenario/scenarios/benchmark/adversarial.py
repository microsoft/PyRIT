# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AdversarialBenchmark scenario — compare attack success rate across adversarial models."""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import Parameter, apply_defaults
from pyrit.executor.attack import AttackScoringConfig
from pyrit.models import AttackOutcome, SeedAttackGroup
from pyrit.registry import AttackTechniqueRegistry, TargetRegistry
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES, _spec_needs_adversarial

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
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

    #: Bumped from 1 → 2 by the refactor that moved adversarial targets
    #: from a constructor parameter to the ``adversarial_targets`` scenario
    #: parameter and changed ``atomic_attack_name`` from
    #: ``{technique}__{model}__{dataset}`` to ``{technique}__{target}_{dataset}``.
    #: ``skip_cached`` only matches against prior runs at the current
    #: ``VERSION``; v1 results remain queryable but won't suppress v2 runs.
    VERSION: int = 2

    #: AdversarialBenchmark compares attack-success rates across adversarial models; a baseline
    #: attack would be model-independent and contribute no signal to the comparison.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

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
            objective_scorer: ``TrueFalseScorer`` used to evaluate attack
                success. Defaults to the registered default objective
                scorer (typically the composite refusal+scale scorer set
                up by an initializer). Widening to general ``Scorer``
                support (covering ``FloatScaleScorer``, etc.) is tracked
                as a follow-up.
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
        Build atomic attacks from (technique × adversarial_target × dataset), then apply caching.

        Reads the user-supplied ``adversarial_targets`` parameter, resolves
        each name to a ``PromptTarget`` via ``TargetRegistry``, and
        cross-products the selected adversarial-capable techniques over the
        resolved targets and configured datasets. Each pair builds a
        non-registered per-pair factory via
        ``AttackTechniqueRegistry.build_factory_from_spec`` with
        ``adversarial_chat`` overridden to the resolved target — no global
        registry state is touched. When ``self._skip_cached`` is set, the
        final candidate list is then filtered against prior completed
        ``(atomic_attack_name, technique_eval_hash)`` tuples.

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
        # ``BenchmarkStrategy`` is built from adversarial-capable
        # ``SCENARIO_TECHNIQUES`` entries only (see ``_build_benchmark_strategy``),
        # so every selected strategy resolves to exactly one spec. Drift between the
        # enum and the catalog is silently ignored — the next strategy-class build
        # would surface it.
        specs_by_name = {spec.name: spec for spec in SCENARIO_TECHNIQUES}
        selected_specs = [specs_by_name[s.value] for s in self._scenario_strategies if s.value in specs_by_name]

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

        cached_pairs = self._collect_cached_completion_pairs()
        filtered = [c for c in atomic_attacks if (c.atomic_attack_name, c.technique_eval_hash) not in cached_pairs]
        skipped = len(atomic_attacks) - len(filtered)
        if skipped > 0:
            logger.info(
                "skip_cached=True: dropping %d/%d atomic attack(s) already completed in prior runs.",
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
        ``_get_atomic_attacks_async`` so a direct ``in`` check filters
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
