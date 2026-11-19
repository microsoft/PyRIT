# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Reusable matrix builder for scenario atomic attacks.

``MatrixAtomicAttackBuilder`` turns a technique × dataset (× optional adversarial
target) grid into a flat list of ``AtomicAttack`` instances. It centralizes the
seed-technique compatibility filtering, ``factory.create`` wiring, ``AtomicAttack``
construction, and baseline emission for scenarios whose attacks form such a
cross-product.

This is one member of a *family* of construction helpers named by shape
(``Matrix...``); scenarios whose construction is composite or per-objective build
``AtomicAttack`` lists differently. There is intentionally no shared builder
interface yet — each scenario calls the builder it needs directly.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast, get_type_hints

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.models import AttackSeedGroup, MessageScorable, ScenarioRunPlanGroupKind, ScenarioRunPlanScorerBinding
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.scenario.core._technique_resolution import (
    TechniqueResolutionError,
    resolve_technique_factories,
    resolve_technique_factories_for_techniques,
)
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.technique_requirements import (
    IncompatibleTechniqueError,
    IncompatibleTechniquePolicy,
    check_target_compatibility,
    filter_seed_groups,
    prepare_seed_group,
)
from pyrit.score import (
    CompatibleScorers,
    NoCompatibleScorerError,
    ScorerMismatch,
    ScorerMismatchKind,
    ScorerRequirements,
    ScorerSelection,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pyrit.converter import Converter
    from pyrit.models import ScoringExpectation
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import Scorer, TrueFalseScorer

logger = logging.getLogger(__name__)

__all__ = [
    "MatrixAtomicAttackBuilder",
    "MatrixCombo",
    "TechniqueResolutionError",
    "build_baseline_atomic_attack",
    "build_baseline_atomic_attacks",
    "build_matrix_atomic_attacks",
    "filter_compatible_seed_groups",
    "resolve_technique_factories",
    "resolve_technique_factories_for_techniques",
]


@dataclass(frozen=True)
class MatrixCombo:
    """
    One cell of the build matrix, passed to the ``name_fn``/``display_group_fn`` callbacks.

    Attributes:
        technique_name (str): The technique (technique enum value) for this cell.
        dataset_name (str): The dataset key from ``DatasetAttackConfiguration.get_attack_groups_by_dataset_async()``.
        target_name (str | None): The adversarial-target registry name when an
            adversarial-target axis is in play, else ``None``.
    """

    technique_name: str
    dataset_name: str
    target_name: str | None = None


def _default_atomic_attack_name(combo: MatrixCombo) -> str:
    """
    Default ``atomic_attack_name`` builder; target-aware so names stay unique.

    Args:
        combo (MatrixCombo): The matrix cell being named.

    Returns:
        str: The atomic attack name for the cell.
    """
    if combo.target_name is None:
        return f"{combo.technique_name}_{combo.dataset_name}"
    return f"{combo.technique_name}__{combo.target_name}_{combo.dataset_name}"


def _default_display_group(combo: MatrixCombo) -> str:
    """
    Build the default display group: aggregate results by technique.

    Args:
        combo (MatrixCombo): The matrix cell being grouped.

    Returns:
        str: The display group for the cell.
    """
    return combo.technique_name


def _unique_group_name(*, name: str, occurrences: Counter[str]) -> str:
    """
    Give repeated, behaviorally identical groups separate execution names.

    Returns:
        str: The name, with an occurrence suffix when needed.
    """
    occurrences[name] += 1
    return name if occurrences[name] == 1 else f"{name}__{occurrences[name]}"


#: Marker for a matrix cell skipped because no objective scorer can judge it.
_SKIPPED = object()


def _binding(selection: ScorerSelection) -> ScenarioRunPlanScorerBinding:
    """
    Record a pool selection for the run plan.

    Returns:
        ScenarioRunPlanScorerBinding: The selected candidate's index, evaluation, and scorer hash.
    """
    return ScenarioRunPlanScorerBinding(
        candidate_index=selection.candidate_index,
        evaluation=selection.evaluation,
        scorer_hash=selection.scorer.get_identifier().hash,
    )


def _narrowed_scoring_config_type(factory: AttackTechniqueFactory) -> type[AttackScoringConfig] | None:
    """
    Return the narrower scoring config type the technique's attack requires, if any.

    Returns:
        type[AttackScoringConfig] | None: The subtype, such as ``TAPAttackScoringConfig``, or None
            when the attack accepts a plain ``AttackScoringConfig``.
    """
    required = factory.scoring_config_type
    if (
        not isinstance(required, type)
        or required is AttackScoringConfig
        or not issubclass(required, AttackScoringConfig)
    ):
        return None
    return required


def _carried_scorer_type(factory: AttackTechniqueFactory) -> type | None:
    """
    Return the objective scorer type the technique's narrowed scoring config declares.

    This is the rule ``AdaptiveScenario`` uses: the config's ``objective_scorer`` annotation
    decides which scorers it can carry, as ``FloatScaleThresholdScorer`` does for TAP.

    Returns:
        type | None: The declared scorer class, or None when any objective scorer is accepted.
    """
    narrowed = _narrowed_scoring_config_type(factory)
    if narrowed is None:
        return None
    declared = get_type_hints(narrowed.__init__).get("objective_scorer")
    return declared if isinstance(declared, type) else None


def _select_carried(
    *,
    pool: CompatibleScorers,
    requirements: ScorerRequirements,
    expectations: Sequence[ScoringExpectation | None],
    carried: type | None,
) -> ScorerSelection:
    """
    Select from the pool candidates whose scorer is an instance of ``carried``.

    Candidates keep their position in the full pool, so pool order still only breaks ties
    between candidates that fit, and a failure gives every candidate's reason.

    Returns:
        ScorerSelection: The selection, indexed against the full pool.

    Raises:
        NoCompatibleScorerError: If no candidate fits.
    """
    if carried is None:
        return pool.select(requirements=requirements, scorable_type=MessageScorable, expectations=expectations)
    positions = [index for index, candidate in enumerate(pool.candidates) if isinstance(candidate.scorer, carried)]
    excluded = [
        ScorerMismatch(
            candidate_index=index,
            candidate_label=candidate.label,
            kind=ScorerMismatchKind.SCORING_CONFIG,
            detail=f"the attack's scoring config only carries a {carried.__name__}.",
        )
        for index, candidate in enumerate(pool.candidates)
        if not isinstance(candidate.scorer, carried)
    ]
    if not positions:
        raise NoCompatibleScorerError(
            requirements=requirements, mismatches=excluded, available_evaluations=pool.evaluations
        )
    fitting = CompatibleScorers(candidates=[pool.candidates[index] for index in positions])
    try:
        selection = fitting.select(requirements=requirements, scorable_type=MessageScorable, expectations=expectations)
    except NoCompatibleScorerError as exc:
        in_pool = [replace(item, candidate_index=positions[item.candidate_index]) for item in exc.mismatches]
        raise NoCompatibleScorerError(
            requirements=requirements,
            mismatches=sorted([*excluded, *in_pool], key=lambda item: item.candidate_index),
            available_evaluations=pool.evaluations,
        ) from exc
    return replace(selection, candidate_index=positions[selection.candidate_index])


def build_baseline_atomic_attack(
    *,
    objective_target: PromptTarget,
    objective_scorer: Scorer,
    seed_groups: list[AttackSeedGroup],
    memory_labels: dict[str, str] | None = None,
    atomic_attack_name: str = "baseline",
    display_group: str | None = None,
    objective_scorer_binding: ScenarioRunPlanScorerBinding | None = None,
) -> AtomicAttack:
    """
    Build a baseline ``AtomicAttack`` that sends each objective unmodified.

    The baseline is a plain ``PromptSendingAttack`` used as a comparison point against
    a scenario's technique attacks. Pass the *same* ``seed_groups`` used to build the
    technique attacks so both populations match — re-resolving under ``max_dataset_size``
    would draw a fresh random sample and diverge from the technique population.

    Args:
        objective_target (PromptTarget): The target to attack.
        objective_scorer (Scorer): The scorer used to evaluate the baseline.
        seed_groups (list[AttackSeedGroup]): Seed groups to attack. Used as-is.
        memory_labels (dict[str, str] | None): Labels applied to the baseline's prompts.
        atomic_attack_name (str): Name for the baseline. Defaults to ``"baseline"``; scenarios
            with multiple scored populations pass e.g. ``"<group>_baseline"``.
        display_group (str | None): Report grouping for the baseline. Defaults to ``None`` so it
            groups on its own; pass a technique cell's ``display_group`` to roll the baseline up
            with that population.
        objective_scorer_binding (ScenarioRunPlanScorerBinding | None): The pool selection of the
            attack population this baseline compares against, for pool runs.

    Returns:
        AtomicAttack: The baseline atomic attack.
    """
    attack = PromptSendingAttack(
        objective_target=objective_target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=cast("TrueFalseScorer", objective_scorer)),
    )
    return AtomicAttack(
        atomic_attack_name=atomic_attack_name,
        attack_technique=AttackTechnique(attack=attack),
        seed_groups=seed_groups,
        objective_scorer=cast("TrueFalseScorer", objective_scorer),
        memory_labels=memory_labels or {},
        display_group=display_group,
        group_kind=ScenarioRunPlanGroupKind.BASELINE,
        objective_scorer_binding=objective_scorer_binding,
    )


def build_baseline_atomic_attacks(
    *,
    objective_target: PromptTarget,
    objective_scorer: Scorer,
    seed_groups: list[AttackSeedGroup],
    memory_labels: dict[str, str] | None = None,
    name: str = "baseline",
    objective_scorer_binding: ScenarioRunPlanScorerBinding | None = None,
) -> list[AtomicAttack]:
    """
    Build baseline units without collapsing repeated objectives.

    Args:
        objective_target (PromptTarget): The target to attack.
        objective_scorer (Scorer): The scorer used to evaluate the baseline.
        seed_groups (list[AttackSeedGroup]): Seed groups to attack. Used as-is.
        memory_labels (dict[str, str] | None): Labels applied to the baseline's prompts.
        name (str): Base name for the units. Defaults to ``"baseline"``.
        objective_scorer_binding (ScenarioRunPlanScorerBinding | None): The pool selection
            this baseline shares with its attack population, for pool runs.

    Returns:
        list[AtomicAttack]: One batch or independently named groups with a shared display group.
    """
    repeated = len({group.objective.value for group in seed_groups}) != len(seed_groups)
    populations = [[group] for group in seed_groups] if repeated else [seed_groups]
    occurrences: Counter[str] = Counter()
    return [
        build_baseline_atomic_attack(
            objective_target=objective_target,
            objective_scorer=objective_scorer,
            seed_groups=population,
            memory_labels=memory_labels,
            atomic_attack_name=(
                _unique_group_name(name=f"{name}__{population[0].logical_id}", occurrences=occurrences)
                if repeated
                else name
            ),
            display_group=name if repeated else None,
            objective_scorer_binding=objective_scorer_binding,
        )
        for population in populations
    ]


def filter_compatible_seed_groups(
    *,
    factory: AttackTechniqueFactory,
    seed_groups: Sequence[AttackSeedGroup],
) -> list[AttackSeedGroup]:
    """
    Apply the matrix builder's seed-technique compatibility rule.

    Returns:
        list[AttackSeedGroup]: Compatible groups in source order.
    """
    retained: list[AttackSeedGroup] = []
    for group in seed_groups:
        try:
            prepared = prepare_seed_group(
                seed_group=group,
                requirements=factory.requirements.seed_group,
                seed_technique=factory.seed_technique,
            )
            factory.validate_group_adversarial_prompts(prepared.seed_group)
        except IncompatibleTechniqueError:
            continue
        retained.append(group)
    return retained


def build_matrix_atomic_attacks(
    *,
    context: ScenarioContext,
    objective_scorer: Scorer | CompatibleScorers,
    display_group_fn: Callable[[MatrixCombo], str] | None = None,
    technique_converters: dict[str, list[Converter]] | None = None,
    extra_factories: dict[str, AttackTechniqueFactory] | None = None,
    scorer_requirements: ScorerRequirements | None = None,
) -> list[AtomicAttack]:
    """
    Build a matrix-shaped scenario's atomic attacks from its resolved context in one call.

    This is the zero-boilerplate path for scenarios whose construction is the plain
    technique × dataset cross-product: it resolves the selected techniques to factories
    (``resolve_technique_factories``) and hands them to ``MatrixAtomicAttackBuilder``
    with the context's target, labels, and per-dataset seed groups. The baseline is emitted
    here alongside the technique attacks when ``context.include_baseline`` is set (the base
    ``Scenario`` no longer emits one centrally).

    Scenarios needing extra axes (adversarial targets, caching, converter stacks) call
    ``MatrixAtomicAttackBuilder`` directly instead.

    This synchronous builder can read attack templates from disk. Async callers
    must offload construction, not their dataset reads or persistence.

    Args:
        context (ScenarioContext): The resolved runtime inputs for this run. Supplies the
            objective target, memory labels, per-dataset seed groups, selected techniques, and
            the resolved ``include_baseline`` flag.
        objective_scorer (Scorer | CompatibleScorers): The scorer applied to each produced atomic
            attack, or an ordered pool to select from per technique and dataset. Not part of the
            context, since it is the scenario's own choice.
        display_group_fn (Callable[[MatrixCombo], str] | None): Builds each ``display_group``.
            Defaults to grouping by technique name.
        technique_converters (dict[str, list[Converter]] | None): Optional override of the
            context's additional request converters. An empty mapping disables them.
        extra_factories (dict[str, AttackTechniqueFactory] | None): Scenario-local factories
            merged on top of the registry (see ``resolve_technique_factories``), so a scenario
            can offer techniques without registering them globally.
        scorer_requirements (ScorerRequirements | None): What the scenario and its resolved run
            configuration require from the objective scorer. Dataset sources add their own.

    Returns:
        list[AtomicAttack]: The generated atomic attacks, baseline first when
        ``context.include_baseline`` is set.
    """
    builder = MatrixAtomicAttackBuilder(
        objective_target=context.objective_target,
        objective_scorer=objective_scorer,
        memory_labels=context.memory_labels,
        incompatible_technique_policy=context.incompatible_technique_policy,
        scorer_requirements=scorer_requirements,
    )
    return builder.build(
        technique_factories=resolve_technique_factories(context=context, extra_factories=extra_factories),
        dataset_groups=context.seed_groups_by_dataset,
        display_group_fn=display_group_fn,
        technique_converters=(context.technique_converters if technique_converters is None else technique_converters),
        include_baseline=context.include_baseline,
        dataset_scorer_requirements=context.dataset_config.get_scorer_requirements_by_dataset(),
    )


class MatrixAtomicAttackBuilder:
    """
    Build ``AtomicAttack`` instances from a technique × dataset (× target) cross-product.

    Construct once with the shared run inputs (target, scorer, labels), then call
    ``build`` with the per-run grid. The builder owns:

    - shared target and full seed-composition compatibility checks,
    - the ``factory.create(...)`` call, forwarding an adversarial target when the
      adversarial-target axis is active,
    - ``AtomicAttack`` construction with naming and display-group stamping, and
    - optional baseline emission using the same resolved seed groups.

    Example:
        >>> builder = MatrixAtomicAttackBuilder(
        ...     objective_target=target,
        ...     objective_scorer=scorer,
        ...     memory_labels=labels,
        ... )
        >>> attacks = builder.build(
        ...     technique_factories=factories,
        ...     dataset_groups=groups,
        ...     include_baseline=True,
        ... )
    """

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        objective_scorer: Scorer | CompatibleScorers,
        memory_labels: dict[str, str] | None = None,
        incompatible_technique_policy: IncompatibleTechniquePolicy = IncompatibleTechniquePolicy.SKIP,
        scorer_requirements: ScorerRequirements | None = None,
    ) -> None:
        """
        Initialize the builder with inputs shared across every atomic attack it produces.

        Args:
            objective_target (PromptTarget): The target system to attack.
            objective_scorer (Scorer | CompatibleScorers): The scorer applied to each produced
                atomic attack and to the baseline, or an ordered pool. With a pool, each
                technique and dataset cell selects the first candidate that meets the combined
                requirements and accepts every prepared input; its baseline uses the same scorer.
                A single scorer faces the same checks.
            memory_labels (dict[str, str] | None): Labels applied to every produced
                atomic attack.
            incompatible_technique_policy (IncompatibleTechniquePolicy): Skip or raise
                for known technique or scorer incompatibility. Other construction errors propagate.
            scorer_requirements (ScorerRequirements | None): What the scenario and resolved run
                configuration require from the objective scorer.

        Raises:
            TypeError: If ``scorer_requirements`` is not ``ScorerRequirements`` or None.
        """
        if scorer_requirements is not None and not isinstance(scorer_requirements, ScorerRequirements):
            raise TypeError("scorer_requirements must be ScorerRequirements or None.")
        self._objective_target = objective_target
        self._objective_scorer = objective_scorer
        self._memory_labels = memory_labels or {}
        self._incompatible_technique_policy = incompatible_technique_policy
        self._scorer_requirements = scorer_requirements
        from pyrit.score import TrueFalseScorer

        self._is_pool = isinstance(objective_scorer, CompatibleScorers)
        # A single true/false scorer is checked as a one-candidate pool. Other scorer types
        # keep the legacy path, where execution validates them.
        self._pool: CompatibleScorers | None = (
            objective_scorer
            if isinstance(objective_scorer, CompatibleScorers)
            else CompatibleScorers.from_scorer(objective_scorer)
            if isinstance(objective_scorer, TrueFalseScorer)
            else None
        )

    def build(
        self,
        *,
        technique_factories: dict[str, AttackTechniqueFactory],
        dataset_groups: Mapping[str, list[AttackSeedGroup]],
        adversarial_targets: Sequence[tuple[str, PromptTarget]] | None = None,
        name_fn: Callable[[MatrixCombo], str] | None = None,
        display_group_fn: Callable[[MatrixCombo], str] | None = None,
        technique_converters: dict[str, list[Converter]] | None = None,
        include_baseline: bool = False,
        dataset_scorer_requirements: Mapping[str, ScorerRequirements] | None = None,
    ) -> list[AtomicAttack]:
        """
        Build the atomic attacks for the given grid.

        Iterates technique → (adversarial target) → dataset. The caller pre-resolves
        ``technique_factories`` to exactly the techniques to build (and, by dict
        insertion order, the order to build them in), so the builder does not need the
        full registry or the selected-technique set. Callers that need to layer static
        guidance onto a technique's adversarial prompt should call
        ``factory.with_adversarial_system_prompt_prefix(...)`` on the relevant factories
        before passing ``technique_factories`` in — the builder stays generic and does
        not forward such a concept itself.

        Named group adversarial prompts configure fresh attack instances. Repeated
        objectives also need separate execution units. Other groups stay batched.
        Group-specific units share the technique's display group. Known prompt-contract
        mismatches follow the configured skip/raise policy before attack construction.

        Args:
            technique_factories (dict[str, AttackTechniqueFactory]): Mapping of technique
                name to the factory that produces it. Only these techniques are built.
            dataset_groups (Mapping[str, list[AttackSeedGroup]]): Mapping of dataset name to
                its seed groups (e.g. ``await DatasetAttackConfiguration.get_attack_groups_by_dataset_async()``).
            adversarial_targets (Sequence[tuple[str, PromptTarget]] | None): Optional
                ``(name, instance)`` pairs adding an adversarial-target axis. When set,
                each technique is swept across every target and the target instance is
                forwarded to ``factory.create(adversarial_chat=...)``. When ``None``, the
                axis is collapsed and each factory uses its own (possibly lazy)
                adversarial target.
            name_fn (Callable[[MatrixCombo], str] | None): Builds each ``atomic_attack_name``.
                Defaults to ``"{technique}_{dataset}"`` (or ``"{technique}__{target}_{dataset}"``
                when an adversarial-target axis is active).
            display_group_fn (Callable[[MatrixCombo], str] | None): Builds each
                ``display_group``. Defaults to grouping by technique name.
            technique_converters (dict[str, list[Converter]] | None): Optional mapping
                from technique name to request converters appended on top of that technique's
                built-in converters (via ``factory.create(extra_request_converters=...)``).
                Techniques absent from the mapping are built unchanged.
            include_baseline (bool): When ``True``, prepend baseline attacks. A single scorer
                compares against the flattened seed groups; a pool builds one baseline per
                dataset and selected scorer, so each attack is compared under the same judge.
                Repeated objectives use separate units.
            dataset_scorer_requirements (Mapping[str, ScorerRequirements] | None): What each
                dataset needs from the objective scorer, keyed by dataset name.

        Returns:
            list[AtomicAttack]: The generated atomic attacks, baseline first when requested.

        Raises:
            ValueError: If no allowed scorer can judge every objective of a dataset, whatever
                the policy, since dropping those objectives would lose requested work silently.
            IncompatibleTechniqueError: If a technique's own scorer needs fit no candidate and
                the policy is RAISE.
        """
        name_fn = name_fn or _default_atomic_attack_name
        display_group_fn = display_group_fn or _default_display_group
        dataset_scorer_requirements = dict(dataset_scorer_requirements or {})

        target_axis: Sequence[tuple[str | None, PromptTarget | None]] = (
            list(adversarial_targets) if adversarial_targets else [(None, None)]
        )

        self._check_datasets_can_be_judged(
            dataset_groups=dataset_groups, dataset_scorer_requirements=dataset_scorer_requirements
        )
        atomic_attacks: list[AtomicAttack] = []
        # Pool runs compare each dataset's attacks with one baseline per selected judge.
        pool_baselines: dict[tuple[str, int], tuple[set[str], ScorerSelection]] = {}
        technique_converters = technique_converters or {}
        for technique_name, factory in technique_factories.items():
            if not check_target_compatibility(
                target=self._objective_target,
                attack_class=factory.attack_class,
                requirements=factory.requirements,
                technique_name=technique_name,
                policy=self._incompatible_technique_policy,
            ):
                continue
            compatible_datasets = {
                name: filter_seed_groups(
                    seed_groups=groups,
                    requirements=factory.requirements.seed_group,
                    seed_technique=factory.seed_technique,
                    technique_name=technique_name,
                    dataset_name=name,
                    policy=self._incompatible_technique_policy,
                    validate_group=factory.validate_group_adversarial_prompts,
                )
                for name, groups in dataset_groups.items()
            }
            extra_converters = technique_converters.get(technique_name)
            extra_request_converters = (
                ConverterConfiguration.from_converters(converters=extra_converters) if extra_converters else None
            )
            # Select each dataset's scorer once; the adversarial-target axis does not change it.
            cells: dict[
                str,
                tuple[list[AttackSeedGroup], TrueFalseScorer, ScenarioRunPlanScorerBinding | None, AttackScoringConfig],
            ] = {}
            for dataset_name, compatible_groups in compatible_datasets.items():
                if not compatible_groups:
                    continue
                selection = self._select_objective_scorer(
                    factory=factory,
                    technique_name=technique_name,
                    dataset_name=dataset_name,
                    seed_groups=compatible_groups,
                    dataset_requirements=dataset_scorer_requirements.get(dataset_name),
                )
                if selection is _SKIPPED:
                    continue
                if isinstance(selection, ScorerSelection):
                    scoring_config = self._scoring_config_for(factory=factory, objective_scorer=selection.scorer)
                    binding = _binding(selection) if self._is_pool else None
                    if self._is_pool:
                        group_ids, _ = pool_baselines.setdefault(
                            (dataset_name, selection.candidate_index), (set(), selection)
                        )
                        group_ids.update(group.logical_id for group in compatible_groups)
                    cells[dataset_name] = (compatible_groups, selection.scorer, binding, scoring_config)
                else:
                    legacy_scorer = cast("TrueFalseScorer", self._objective_scorer)
                    cells[dataset_name] = (
                        compatible_groups,
                        legacy_scorer,
                        None,
                        AttackScoringConfig(objective_scorer=legacy_scorer),
                    )
            for target_name, target_instance in target_axis:
                for dataset_name, (compatible_groups, objective_scorer, binding, scoring_config) in cells.items():
                    combo = MatrixCombo(
                        technique_name=technique_name,
                        dataset_name=dataset_name,
                        target_name=target_name,
                    )
                    per_group = any(group.adversarial_prompts is not None for group in compatible_groups) or len(
                        {group.objective.value for group in compatible_groups}
                    ) != len(compatible_groups)
                    populations = [[group] for group in compatible_groups] if per_group else [compatible_groups]
                    occurrences: Counter[str] = Counter()
                    for population in populations:
                        group = population[0]
                        attack_technique = factory.create(
                            objective_target=self._objective_target,
                            attack_scoring_config=scoring_config,
                            adversarial_chat=target_instance,
                            extra_request_converters=extra_request_converters,
                            seed_group=group if per_group else None,
                        )
                        name = name_fn(combo)
                        if per_group:
                            name = _unique_group_name(name=f"{name}__{group.logical_id}", occurrences=occurrences)
                        atomic_attacks.append(
                            AtomicAttack(
                                atomic_attack_name=name,
                                attack_technique=attack_technique,
                                seed_groups=population,
                                adversarial_chat=(
                                    target_instance
                                    if target_instance is not None
                                    else factory.resolve_adversarial_chat()
                                ),
                                objective_scorer=objective_scorer,
                                memory_labels=self._memory_labels,
                                display_group=display_group_fn(combo),
                                technique_name=technique_name,
                                objective_scorer_binding=binding,
                            )
                        )

        if include_baseline:
            if isinstance(self._objective_scorer, CompatibleScorers):
                baselines = self._build_pool_baselines(pool_baselines=pool_baselines, dataset_groups=dataset_groups)
            else:
                all_seed_groups = [group for groups in dataset_groups.values() for group in groups]
                baselines = build_baseline_atomic_attacks(
                    objective_target=self._objective_target,
                    objective_scorer=self._objective_scorer,
                    seed_groups=all_seed_groups,
                    memory_labels=self._memory_labels,
                )
            atomic_attacks = [*baselines, *atomic_attacks]

        return atomic_attacks

    def _check_datasets_can_be_judged(
        self,
        *,
        dataset_groups: Mapping[str, list[AttackSeedGroup]],
        dataset_scorer_requirements: Mapping[str, ScorerRequirements],
    ) -> None:
        """
        Require an allowed scorer that can judge every objective of each dataset, as given.

        This is about the dataset, not a technique, so it ignores the incompatible technique
        policy: skipping would drop requested objectives silently. Malformed inputs raise here too.

        Raises:
            ValueError: If no candidate meeting the scenario's and the dataset's requirements can
                judge all of a dataset's objectives.
        """
        if self._pool is None:
            return
        for dataset_name, groups in dataset_groups.items():
            try:
                self._pool.select(
                    requirements=ScorerRequirements.combine(
                        self._scorer_requirements, dataset_scorer_requirements.get(dataset_name)
                    ),
                    scorable_type=MessageScorable,
                    expectations=[group.scoring_expectation for group in groups],
                )
            except NoCompatibleScorerError as exc:
                raise ValueError(f"No objective scorer can judge dataset '{dataset_name}'.\n{exc}") from exc

    def _scoring_config_for(
        self, *, factory: AttackTechniqueFactory, objective_scorer: TrueFalseScorer
    ) -> AttackScoringConfig:
        """
        Build the scoring config the technique's attack accepts, carrying the chosen scorer.

        An attack that narrows its config type, as TAP does with ``TAPAttackScoringConfig``,
        drops a plain config and judges with its own default scorer. A pool run records which
        scorer it selected, so the narrowed type is built around that scorer instead. Selection
        only picks a scorer of the type that config declares, so construction errors propagate.
        Single-scorer runs keep the plain config.

        Returns:
            AttackScoringConfig: The config to pass to ``factory.create``.
        """
        narrowed = _narrowed_scoring_config_type(factory) if self._is_pool else None
        if narrowed is None:
            return AttackScoringConfig(objective_scorer=objective_scorer)
        return narrowed(objective_scorer=objective_scorer)

    def _select_objective_scorer(
        self,
        *,
        factory: AttackTechniqueFactory,
        technique_name: str,
        dataset_name: str,
        seed_groups: Sequence[AttackSeedGroup],
        dataset_requirements: ScorerRequirements | None,
    ) -> ScorerSelection | object | None:
        """
        Choose the objective scorer for one technique and dataset cell.

        Requirements combine the scenario's, the technique's, and the dataset's. Inputs are the
        prepared execution copies, so technique seeds are composed exactly as they will run.
        In a pool run, a candidate the technique's scoring config cannot carry is passed over.
        Each dataset already has a scorer for its objectives, so a mismatch here comes from the
        technique and follows the incompatible technique policy.

        Returns:
            ScorerSelection | object | None: The selection; ``_SKIPPED`` when the cell is skipped;
                None on the legacy path for scorers that are not true/false scorers.

        Raises:
            IncompatibleTechniqueError: If no candidate meets the technique's needs and the policy is RAISE.
        """
        if self._pool is None:
            return None
        requirements = ScorerRequirements.combine(
            self._scorer_requirements, factory.requirements.objective_scorer, dataset_requirements
        )
        expectations = [
            prepare_seed_group(
                seed_group=group,
                requirements=factory.requirements.seed_group,
                seed_technique=factory.seed_technique,
            ).seed_group.scoring_expectation
            for group in seed_groups
        ]
        try:
            return _select_carried(
                pool=self._pool,
                requirements=requirements,
                expectations=expectations,
                carried=_carried_scorer_type(factory) if self._is_pool else None,
            )
        except NoCompatibleScorerError as exc:
            message = f"Technique '{technique_name}', dataset '{dataset_name}': {exc}"
            if self._incompatible_technique_policy is IncompatibleTechniquePolicy.RAISE:
                raise IncompatibleTechniqueError(message) from exc
            logger.warning("Skipping %s", message)
            return _SKIPPED

    def _build_pool_baselines(
        self,
        *,
        pool_baselines: Mapping[tuple[str, int], tuple[set[str], ScorerSelection]],
        dataset_groups: Mapping[str, list[AttackSeedGroup]],
    ) -> list[AtomicAttack]:
        """
        Build one baseline per dataset and selected scorer, over every group that scorer judges.

        Each objective is sent once per judge, however many techniques use it. A dataset with
        one judge has ``baseline_<dataset>``; with several, each name carries the candidate's
        pool index, so names don't depend on technique order.

        Returns:
            list[AtomicAttack]: The baseline units, by dataset order, then pool order.
        """
        judges_per_dataset = Counter(dataset_name for dataset_name, _ in pool_baselines)
        baselines: list[AtomicAttack] = []
        for dataset_name, groups in dataset_groups.items():
            for (name, candidate_index), (group_ids, selection) in sorted(pool_baselines.items()):
                if name != dataset_name:
                    continue
                baseline_name = f"baseline_{dataset_name}"
                if judges_per_dataset[dataset_name] > 1:
                    baseline_name = f"{baseline_name}__{candidate_index}"
                baselines.extend(
                    build_baseline_atomic_attacks(
                        objective_target=self._objective_target,
                        objective_scorer=selection.scorer,
                        seed_groups=[group for group in groups if group.logical_id in group_ids],
                        memory_labels=self._memory_labels,
                        name=baseline_name,
                        objective_scorer_binding=_binding(selection),
                    )
                )
        return baselines
