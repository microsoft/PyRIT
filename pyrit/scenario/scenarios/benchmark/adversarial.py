# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""AdversarialBenchmark scenario — compare attack success rate across adversarial models."""

from __future__ import annotations

import logging
import uuid
from functools import cache
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.analytics import get_cached_results_for_technique
from pyrit.common import apply_defaults
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.common.utils import to_sha256
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ObjectiveTargetEvaluationIdentifier,
    ScenarioResult,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScorerEvaluationIdentifier,
)
from pyrit.models.parameter import Parameter
from pyrit.registry import AttackTechniqueRegistry, TargetRegistry
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import (
    MatrixAtomicAttackBuilder,
    filter_compatible_seed_groups,
    resolve_technique_factories,
    resolve_technique_factories_for_techniques,
)
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.models import AttackSeedGroup
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


logger = logging.getLogger(__name__)


@cache
def _build_benchmark_technique() -> type[ScenarioTechnique]:
    """
    Build the ``BenchmarkTechnique`` enum from the registered factory catalog.

    Reads adversarial-capable factories from the
    ``AttackTechniqueRegistry`` singleton and passes them to
    ``build_technique_class_from_factories``. Factories that bake their own
    ``adversarial_chat`` are excluded — the benchmark sweeps each technique
    across the user-supplied targets, which is incompatible with a technique
    that pins its own adversarial target. Which techniques are registered is
    decided by the active initializer (the registration gate); this scenario
    does not narrow the pool further by group. The resulting enum has one
    concrete member per factory (e.g. ``red_teaming``, ``tap``,
    ``crescendo_simulated``) and a ``light`` / ``single_turn`` / ``multi_turn``
    aggregate for each catalog tag. The scenario's default run is the explicit
    ``role_play_video_game`` / ``crescendo_simulated`` / ``tap`` set.

    The (technique × target) cross-product is materialized lazily in
    ``AdversarialBenchmark._build_atomic_attacks_async`` from the
    user-supplied ``adversarial_targets`` parameter.

    Returns:
        type[ScenarioTechnique]: The dynamically generated ``BenchmarkTechnique`` class.
    """
    registry = AttackTechniqueRegistry.get_registry_singleton()
    factories = [
        factory
        for factory in registry.get_factories_or_raise().values()
        if factory.uses_adversarial and factory.adversarial_chat is None
    ]
    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[ty:invalid-return-type]
        class_name="BenchmarkTechnique",
        factories=factories,
        default_names={"role_play_video_game", "crescendo_simulated", "tap"},
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
    selected adversarial-capable factory in the
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
    #: Bumped from 2 → 3 by dropping the ``core`` pool gate so the selectable
    #: technique pool (and therefore the ``all`` aggregate) reflects whatever the
    #: initializer registered rather than only core-tagged factories.
    #: Bumped from 3 → 4 when the no-selection default changed from the ``light``
    #: aggregate to ``role_play_video_game``, ``crescendo_simulated``, and ``tap``.
    #: Bumped from 4 → 5 when objective scoring changed from the registry-selected
    #: default to task-achievement evaluation that supports the benchmark's broad
    #: behavior taxonomy.
    #: ``VERSION`` participates in resume identity, so older results cannot be resumed
    #: as v5. Cache reuse also requires this version so implementation changes cannot
    #: silently reuse results produced by an incompatible benchmark definition.
    VERSION: int = 5

    #: AdversarialBenchmark compares attack-success rates across adversarial models; a baseline
    #: attack would be model-independent and contribute no signal to the comparison.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden
    _TAP_PARAMETER_MAP: ClassVar[tuple[tuple[str, str], ...]] = (
        ("tap_tree_width", "tree_width"),
        ("tap_tree_depth", "tree_depth"),
        ("tap_branching_factor", "branching_factor"),
        ("tap_batch_size", "batch_size"),
    )

    @classmethod
    def _get_additional_scoring_questions(cls) -> list[Path]:
        """
        Use objective fulfillment rather than a harm-category classifier.

        Returns:
            list[Path]: The task-achievement scoring rubric.
        """
        return [SCORER_SEED_PROMPT_PATH / "true_false_question" / "task_achieved_refined.yaml"]

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare benchmark-specific runtime parameters.

        The list is treated as required at run time:
        ``_build_atomic_attacks_async`` raises ``ValueError`` if
        ``self.params["adversarial_targets"]`` is empty or missing. The
        scenario-side error (rather than a declaration-side default) lets
        the caller raise a domain-specific message that names the CLI flag,
        the ``.pyrit_conf`` key, and ``pyrit_scan list-targets``.

        Returns:
            list[Parameter]: Parameters for adversarial targets and cache reuse.
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
            Parameter(
                name="use_cached",
                description=(
                    "Reuse completed results from compatible prior benchmark runs. "
                    "Defaults to false; set with --use-cached true on the CLI."
                ),
                param_type=bool,
                default=None,
            ),
            Parameter(
                name="tap_tree_width",
                description="Override TAP's retained tree width. Leave unset to use the registered technique default.",
                param_type=int,
                default=None,
            ),
            Parameter(
                name="tap_tree_depth",
                description="Override TAP's maximum tree depth. Leave unset to use the registered technique default.",
                param_type=int,
                default=None,
            ),
            Parameter(
                name="tap_branching_factor",
                description="Override TAP's branching factor. Leave unset to use the registered technique default.",
                param_type=int,
                default=None,
            ),
            Parameter(
                name="tap_batch_size",
                description=(
                    "Override TAP's internal node batch size. Leave unset to use the registered technique default."
                ),
                param_type=int,
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
                success. Defaults to task-achievement evaluation with a
                refusal backstop so objectives outside Azure Content Safety's
                four harm categories are evaluated correctly. Widening to
                general ``Scorer`` support (covering ``FloatScaleScorer``,
                etc.) is tracked as a follow-up.
            use_cached: Backward-compatible programmatic default for cache reuse.
                The runtime ``use_cached`` parameter overrides it when supplied.
                Reuse is disabled when both are omitted.
            scenario_result_id: Optional ID of an existing scenario result
                to resume.
        """
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )
        self._constructor_use_cached: bool = use_cached
        self._use_cached: bool = use_cached
        self._precomputed_cached_results: dict[str, list[AttackResult]] = {}
        self._cached_results_by_name: dict[str, list[AttackResult]] = {}
        self._initial_objective_hashes: list[str] = []

        technique_class = _build_benchmark_technique()

        super().__init__(
            version=self.VERSION,
            objective_scorer=self._objective_scorer,
            technique_class=technique_class,
            default_dataset_config=DatasetAttackConfiguration(
                dataset_names=["harmbench"],
                max_dataset_size=8,
            ),
            scenario_result_id=scenario_result_id,
        )

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        """
        Resolve a stable objective subset when cross-run cache reuse is enabled.

        Args:
            apply_sampling: Whether to apply the configured global dataset limit.

        Returns:
            dict[str, list[AttackSeedGroup]]: Attack groups keyed by dataset name.
        """
        if not apply_sampling or not self._is_cache_reuse_enabled():
            return await super()._resolve_seed_groups_by_dataset_async(apply_sampling=apply_sampling)

        groups_by_dataset = await super()._resolve_seed_groups_by_dataset_async(apply_sampling=False)
        max_dataset_size = self._dataset_config.max_dataset_size
        pairs = [(name, group) for name, groups in groups_by_dataset.items() for group in groups]
        if max_dataset_size is None or len(pairs) <= max_dataset_size:
            return groups_by_dataset

        selected = sorted(
            pairs,
            key=lambda pair: self._get_cache_sampling_key(dataset_name=pair[0], seed_group=pair[1]),
        )[:max_dataset_size]
        sampled: dict[str, list[AttackSeedGroup]] = {}
        for dataset_name, seed_group in selected:
            sampled.setdefault(dataset_name, []).append(seed_group)
        return sampled

    @staticmethod
    def _get_cache_sampling_key(*, dataset_name: str, seed_group: AttackSeedGroup) -> str:
        """
        Return a stable rank for cache-compatible objective sampling.

        Args:
            dataset_name: Dataset that owns the attack group.
            seed_group: Attack group containing the objective.

        Returns:
            str: Content-derived SHA-256 rank.

        Raises:
            ValueError: If the attack group has no objective.
        """
        objective = seed_group.objective
        if objective is None:
            raise ValueError(f"Dataset '{dataset_name}' produced an attack group without an objective.")
        return to_sha256(f"{dataset_name}\0{objective.value}")

    def _is_cache_reuse_enabled(self) -> bool:
        """Return the effective constructor/runtime cache setting."""
        runtime_use_cached = self.params.get("use_cached")
        return self._constructor_use_cached if runtime_use_cached is None else runtime_use_cached

    async def _estimate_run_size_async(self) -> ScenarioRunSizeEstimate:
        """
        Estimate the target-by-technique matrix using execution compatibility.

        Returns:
            ScenarioRunSizeEstimate: Structured benchmark estimate.
        """
        selected_groups, datasets = await self._resolve_dataset_groups_for_estimate_async()
        factories = resolve_technique_factories_for_techniques(
            scenario_techniques=self._scenario_techniques,
            extra_factories=self._get_tap_factory_override(),
        )
        per_target_components: list[ScenarioRunSizeComponent] = []
        for technique in self._scenario_techniques:
            factory = factories.get(technique.value)
            if factory is None:
                continue
            compatible_count = sum(
                len(filter_compatible_seed_groups(factory=factory, seed_groups=groups))
                for groups in selected_groups.values()
            )
            per_target_components.append(
                ScenarioRunSizeComponent(
                    label=technique.value,
                    count=compatible_count,
                    note="Count per adversarial target.",
                )
            )

        compatibility_bounds = (
            self._get_technique_compatibility_bounds(datasets=datasets) if self._estimate_has_binding_size_cap else None
        )
        sampled_per_target_count = sum(component.count for component in per_target_components)
        if compatibility_bounds is not None:
            per_target_minimum = sum(bounds[0] for bounds in compatibility_bounds.values())
            per_target_maximum = sum(bounds[1] for bounds in compatibility_bounds.values())
        elif self._estimate_has_binding_size_cap:
            per_target_minimum = None
            per_target_maximum = None
        else:
            per_target_minimum = sampled_per_target_count
            per_target_maximum = sampled_per_target_count
        target_names = self.params.get("adversarial_targets") or []
        if not target_names:
            return ScenarioRunSizeEstimate(
                minimum_attack_count=per_target_minimum,
                components=per_target_components,
                datasets=datasets,
                note=(
                    "Counts are per adversarial target. At least one adversarial_targets entry is required, "
                    "and the total scales with the number of entries supplied. Baseline is forbidden."
                ),
            )

        resolved_targets = self._resolve_adversarial_targets(target_names=target_names)
        target_count = len(resolved_targets)
        components = [
            component.model_copy(update={"count": component.count * target_count, "note": None})
            for component in per_target_components
        ]
        if self._is_cache_reuse_enabled():
            return ScenarioRunSizeEstimate(
                minimum_attack_count=0,
                maximum_attack_count=per_target_maximum * target_count if per_target_maximum is not None else None,
                components=components,
                datasets=datasets,
                note=(
                    "Components describe the candidate population. Live behavioral-cache hits can suppress work, "
                    "so the authoritative total is unavailable before launch."
                ),
            )
        if self._estimate_has_binding_size_cap and compatibility_bounds is None:
            return ScenarioRunSizeEstimate(
                components=components,
                datasets=datasets,
                note=(
                    "Components describe the sampled candidate population. A binding randomized dataset cap may "
                    "select a different compatibility mix at launch."
                ),
            )
        if (
            self._estimate_has_binding_size_cap
            and per_target_minimum is not None
            and per_target_maximum is not None
            and per_target_minimum != per_target_maximum
        ):
            return ScenarioRunSizeEstimate(
                minimum_attack_count=per_target_minimum * target_count,
                maximum_attack_count=per_target_maximum * target_count,
                components=components,
                datasets=datasets,
                note=(
                    "The range covers every compatibility mix that the randomized per-dataset caps can select. "
                    "Baseline is forbidden."
                ),
            )
        return ScenarioRunSizeEstimate(
            estimated_attack_count=sum(component.count for component in components),
            components=components,
            datasets=datasets,
            note="Baseline is forbidden; retries and internal attack turns are excluded.",
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build atomic attacks from (technique × adversarial_target × dataset), then apply caching.

        Reads the user-supplied ``adversarial_targets`` parameter, resolves each name to a
        ``PromptTarget`` via ``TargetRegistry``, and delegates the
        ``(technique × target × dataset)`` cross-product to ``MatrixAtomicAttackBuilder``
        with the resolved targets as its adversarial-target axis. Each pair calls
        ``factory.create(adversarial_chat=...)`` with the resolved target — no global
        registry state is touched. When cache reuse is enabled, exact compatible
        prior results are retained for the final scenario result and only their
        corresponding objective seed groups are removed from execution.

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
        technique_factories = resolve_technique_factories(
            context=context,
            extra_factories=self._get_tap_factory_override(),
        )

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
            include_baseline=context.include_baseline,
        )
        self._initial_objective_hashes = list(
            dict.fromkeys(to_sha256(objective) for attack in atomic_attacks for objective in attack.objectives)
        )

        self._use_cached = self._is_cache_reuse_enabled()
        if not self._use_cached or self._scenario_result_id:
            return atomic_attacks

        self._apply_reusable_cached_results(atomic_attacks=atomic_attacks)
        return atomic_attacks

    def _get_tap_factory_override(self) -> dict[str, AttackTechniqueFactory] | None:
        """
        Build a scenario-local TAP factory when search parameters are overridden.

        Returns:
            dict[str, AttackTechniqueFactory] | None: A TAP factory override, or
                ``None`` when the registered TAP defaults should remain unchanged.
        """
        attack_kwargs = {
            attack_parameter: self.params[scenario_parameter]
            for scenario_parameter, attack_parameter in self._TAP_PARAMETER_MAP
            if self.params.get(scenario_parameter) is not None
        }
        if not attack_kwargs:
            return None

        registered_factory = AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise().get("tap")
        return (
            {"tap": registered_factory.with_attack_kwargs(attack_kwargs=attack_kwargs)} if registered_factory else None
        )

    def _build_initial_scenario_metadata(self) -> dict[str, Any]:
        """
        Preserve the sampled objective set before cross-run cache pruning.

        Returns:
            dict[str, Any]: Scenario metadata with the complete sampled objective set.
        """
        metadata = super()._build_initial_scenario_metadata()
        if getattr(self._dataset_config, "max_dataset_size", None) is not None:
            metadata["objective_hashes"] = list(self._initial_objective_hashes)
        return metadata

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
        Persist compatible cached results into this run, then execute uncached objectives.

        Cached results are copied with new result IDs and attributed to the new
        scenario result. This keeps database-backed status, API responses, and
        later artifact exports complete without moving results away from their
        original runs.

        Returns:
            ScenarioResult: The persisted scenario result containing cached and
            newly executed objective results.
        """
        try:
            self._persist_precomputed_cached_results()
        except Exception as error:
            if self._scenario_result_id:
                self._mark_scenario_failed(scenario_result_id=self._scenario_result_id, error=error)
            raise
        return await super().run_async()

    def _apply_reusable_cached_results(self, *, atomic_attacks: list[AtomicAttack]) -> None:
        """
        Remove only objectives having an exact reusable result.

        Args:
            atomic_attacks: Candidate attacks whose seed groups may be pruned.
        """
        self._precomputed_cached_results = {}
        reusable = self._collect_reusable_cached_results(atomic_attacks=atomic_attacks)
        for attack in atomic_attacks:
            prior_results = reusable.get(attack.atomic_attack_name, [])
            if not prior_results:
                continue
            attack.drop_seed_groups_with_hashes(hashes={to_sha256(result.objective) for result in prior_results})
            self._precomputed_cached_results[attack.atomic_attack_name] = prior_results

        cached_count = sum(len(results) for results in reusable.values())
        if cached_count:
            fully_cached_count = sum(not attack.seed_groups for attack in atomic_attacks)
            logger.info(
                "use_cached=True: reusing %d objective result(s) across %d atomic attack(s); "
                "%d atomic attack(s) are fully cached.",
                cached_count,
                len(reusable),
                fully_cached_count,
            )

    def _collect_reusable_cached_results(self, *, atomic_attacks: list[AtomicAttack]) -> dict[str, list[AttackResult]]:
        """
        Select the newest exact compatible result for each objective.

        Reuse requires matching objective content, technique/system-prompt identity,
        objective-target identity, effective outcome scorer, atomic-attack slot, and
        benchmark class/version. ``ERROR`` and ``UNDETERMINED`` rows are never reused.

        Args:
            atomic_attacks: Candidate attacks for this run.

        Returns:
            dict[str, list[AttackResult]]: Reusable results keyed by atomic attack name.
        """
        candidate_names = self._collect_cached_completion_pairs(atomic_attacks=atomic_attacks)
        candidate_results = [
            result for name in candidate_names for result in self._cached_results_by_name.get(name, [])
        ]
        compatible_parent_ids = self._get_compatible_cache_parent_ids(results=candidate_results)
        reusable: dict[str, list[AttackResult]] = {}

        for attack in atomic_attacks:
            if attack.atomic_attack_name not in candidate_names:
                continue
            expected_scorer_hash = self._get_attack_scorer_eval_hash(atomic_attack=attack)
            if expected_scorer_hash is None:
                continue
            objectives_by_hash = {to_sha256(objective): objective for objective in attack.objectives}
            selected_by_hash: dict[str, AttackResult] = {}
            for result in self._cached_results_by_name.get(attack.atomic_attack_name, []):
                objective_hash = to_sha256(result.objective)
                if objective_hash in selected_by_hash:
                    continue
                if result.outcome not in (AttackOutcome.SUCCESS, AttackOutcome.FAILURE):
                    continue
                if result.attribution_parent_id not in compatible_parent_ids:
                    continue
                if objectives_by_hash.get(objective_hash) != result.objective:
                    continue
                if self._get_result_scorer_eval_hash(result=result) != expected_scorer_hash:
                    continue
                selected_by_hash[objective_hash] = result
            if selected_by_hash:
                reusable[attack.atomic_attack_name] = [
                    selected_by_hash[to_sha256(objective)]
                    for objective in attack.objectives
                    if to_sha256(objective) in selected_by_hash
                ]
        return reusable

    def _get_compatible_cache_parent_ids(self, *, results: list[AttackResult]) -> set[str]:
        """
        Return parent scenario IDs produced by this benchmark version.

        Args:
            results: Cached candidates whose parent scenarios should be checked.

        Returns:
            set[str]: IDs of compatible parent scenario results.
        """
        parent_ids = sorted({result.attribution_parent_id for result in results if result.attribution_parent_id})
        if not parent_ids:
            return set()
        parent_results = self._memory.get_scenario_results(
            scenario_result_ids=parent_ids,
            scenario_name=type(self).__name__,
            scenario_version=self.VERSION,
        )
        return {
            str(result.id)
            for result in parent_results
            if result.scenario_name == type(self).__name__
            and result.scenario_identifier.class_module == type(self).__module__
            and result.scenario_version == self.VERSION
        }

    @staticmethod
    def _get_attack_scorer_eval_hash(*, atomic_attack: AtomicAttack) -> str | None:
        """
        Return the effective outcome scorer hash for an atomic attack.

        Args:
            atomic_attack: Attack whose configured scorer should be identified.

        Returns:
            str | None: Scorer evaluation hash, or None when no scorer is configured.
        """
        technique_identifier = atomic_attack.attack_technique.get_identifier()
        attack_identifier = technique_identifier.get_child("attack")
        scorer_identifier = attack_identifier.get_child("objective_scorer") if attack_identifier else None
        return ScorerEvaluationIdentifier(scorer_identifier).eval_hash if scorer_identifier else None

    @staticmethod
    def _get_result_scorer_eval_hash(*, result: AttackResult) -> str | None:
        """
        Return the scorer hash that determined a cached result's outcome.

        Args:
            result: Cached result to inspect.

        Returns:
            str | None: Scorer evaluation hash, or None when the final score has no identifier.
        """
        scorer_identifier = result.last_score.scorer_class_identifier if result.last_score else None
        return ScorerEvaluationIdentifier(scorer_identifier).eval_hash if scorer_identifier else None

    def _persist_precomputed_cached_results(self) -> None:
        """
        Copy reusable results into the current scenario result.

        Raises:
            ValueError: If the scenario result has not been initialized.
        """
        if not self._precomputed_cached_results:
            return
        if not self._scenario_result_id:
            raise ValueError("Cannot persist cached results before the scenario result is initialized.")

        copies: list[AttackResult] = []
        for attack_name, results in self._precomputed_cached_results.items():
            for result in results:
                attribution_data = dict(result.attribution_data or {})
                attribution_data["parent_collection"] = attack_name
                metadata = dict(result.metadata)
                metadata.setdefault("cached_from_attack_result_id", result.attack_result_id)
                copies.append(
                    result.model_copy(
                        deep=True,
                        update={
                            "attack_result_id": str(uuid.uuid4()),
                            "attribution_parent_id": self._scenario_result_id,
                            "attribution_data": attribution_data,
                            "labels": {**result.labels, **self._memory_labels},
                            "metadata": metadata,
                        },
                    )
                )
        self._memory.add_attack_results_to_memory(attack_results=copies)
        self._precomputed_cached_results = {}

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
        ``_build_atomic_attacks_async`` can persist them into the final ``ScenarioResult``
        via ``run_async`` without re-filtering.

        Args:
            atomic_attacks: The candidate atomic attacks built earlier in
                ``_build_atomic_attacks_async``.

        Returns:
            set[str]: ``atomic_attack_name`` values that have at least one qualifying cached
            ``AttackResult``. Empty set when the scenario has no objective target identifier
            or no compatible result exists.
        """
        cached_names: set[str] = set()
        self._cached_results_by_name: dict[str, list[AttackResult]] = {}

        if self._objective_target_identifier is None:
            return cached_names

        objective_target_eval_hash = ObjectiveTargetEvaluationIdentifier(self._objective_target_identifier).eval_hash

        unique_technique_hashes = {c.technique_eval_hash for c in atomic_attacks if c.technique_eval_hash}

        # One DB query per unique hash (deduplication), results stored temporarily by hash.
        raw_results_by_hash: dict[str, list[AttackResult]] = {}
        for technique_eval_hash in unique_technique_hashes:
            raw_results_by_hash[technique_eval_hash] = get_cached_results_for_technique(
                self._memory,
                technique_eval_hash=technique_eval_hash,
                objective_target_eval_hash=objective_target_eval_hash,
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
