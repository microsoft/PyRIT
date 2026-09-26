# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Incremental read model for persisted scenario progress."""

import logging
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

from pyrit.analytics.scenario_statistics import (
    ScenarioPlanLookup,
    compute_scenario_statistics,
    count_execution_units,
    resolve_attack_result_attempt,
    resolve_execution_unit,
    retry_pressure,
)
from pyrit.common.utils import to_sha256
from pyrit.memory import AttackResultKeysetCursor
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import (
    AttackResult,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    ScenarioAtomicGroupProgress,
    ScenarioAttackResultDelta,
    ScenarioAttackTechniqueDetails,
    ScenarioComponentIdentity,
    ScenarioDisplayGroupProgress,
    ScenarioExecutionUnit,
    ScenarioObjectiveScorer,
    ScenarioObjectiveScorerMetrics,
    ScenarioProgressCounts,
    ScenarioProgressResult,
    ScenarioProgressSummary,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioScorerIdentity,
    ScenarioSeedGroupProgress,
    ScenarioTechniqueProgress,
    ScorerEvaluationIdentifier,
    ScorerIdentifier,
    project_behavioral_identity,
)
from pyrit.score.scorer_evaluation.scorer_metrics_io import find_objective_metrics_by_eval_hash

logger = logging.getLogger(__name__)

# Execution-unit identity and plan lookup live in ``pyrit.analytics`` so the SDK, backend, and reports
# share one implementation. These names remain importable from here for compatibility.
ResultUnitIdentity = ScenarioExecutionUnit
__all__ = ["ResultUnitIdentity", "ScenarioPlanLookup", "ScenarioProgressReadModel", "ScenarioProgressSnapshot"]

# Technique seeds are rendered as content, so the REST payload carries only the
# fields the UI displays. All other narrowing is declared by identifier types and
# applied by ``project_behavioral_identity``.
_TECHNIQUE_SEEDS_CHILD = "technique_seeds"
_TECHNIQUE_SEED_DISPLAY_PARAMS = ("value", "data_type")


@dataclass(frozen=True, slots=True)
class ScenarioProgressSnapshot:
    """Immutable boundary returned after refreshing one run's progress state."""

    deltas: tuple[ScenarioAttackResultDelta, ...]
    results: tuple[ScenarioProgressResult, ...]
    summary: ScenarioProgressSummary
    plan: ScenarioRunPlan


@dataclass(frozen=True, slots=True)
class _ProgressSummaryState:
    """Inputs that determine whether a cached progress summary remains valid."""

    active_group_ids: tuple[str, ...]
    terminal: bool
    plan_complete: bool


@dataclass
class _ProgressCacheEntry:
    """Hydrated and mapped progress state for one scenario run."""

    plan_signature: str | None = None
    deltas: list[ScenarioAttackResultDelta] = field(default_factory=list)
    results: list[ScenarioProgressResult] = field(default_factory=list)
    cursor: AttackResultKeysetCursor | None = None
    summary: ScenarioProgressSummary | None = None
    summary_state: _ProgressSummaryState | None = None


class ScenarioProgressReadModel:
    """Hydrate, map, cache, and summarize persisted scenario progress."""

    _CACHE_MAX_RUNS = 32
    _STORAGE_PAGE_SIZE = 500

    def __init__(self, *, memory: MemoryInterface) -> None:
        """Initialize a read model over the scenario-result store."""
        self._memory = memory
        self._cache: OrderedDict[str, _ProgressCacheEntry] = OrderedDict()
        self._cache_lock = Lock()

    def get_snapshot(
        self,
        *,
        scenario_result_id: str,
        plan: ScenarioRunPlan | None,
        plan_complete: bool,
        active_group_ids: Sequence[str],
        terminal: bool,
        objective_scorer_identifier: ComponentIdentifier | None,
    ) -> ScenarioProgressSnapshot:
        """
        Refresh and return the mapped progress state for one run.

        Returns:
            ScenarioProgressSnapshot: Deltas, mapped results, summary, and effective plan.
        """
        plan_signature = plan.model_dump_json() if plan is not None else None
        with self._cache_lock:
            entry = self._cache.get(scenario_result_id)
            if entry is not None and entry.plan_signature == plan_signature:
                has_unenriched_identifier = any(
                    delta.atomic_attack_identifier is not None and not delta.atomic_attack_identifier.seed_identifiers
                    for delta in entry.deltas
                )
                was_terminal = entry.summary_state is not None and entry.summary_state.terminal
                if has_unenriched_identifier and (not terminal or not was_terminal):
                    entry = None
            if entry is None or entry.plan_signature != plan_signature:
                entry = _ProgressCacheEntry(plan_signature=plan_signature)
                self._cache[scenario_result_id] = entry
            self._cache.move_to_end(scenario_result_id)
            while len(self._cache) > self._CACHE_MAX_RUNS:
                self._cache.popitem(last=False)

            first_new_index = len(entry.deltas)
            self._hydrate_new_deltas(scenario_result_id=scenario_result_id, entry=entry)

            summary_plan = plan or self._synthesize_legacy_plan(deltas=entry.deltas)
            if len(entry.results) < len(entry.deltas):
                plan_lookup = ScenarioPlanLookup.from_plan(plan=summary_plan)
                entry.results.extend(
                    self._map_progress_delta(delta=delta, plan_lookup=plan_lookup)
                    for delta in entry.deltas[len(entry.results) :]
                )

            summary_state = _ProgressSummaryState(
                active_group_ids=tuple(active_group_ids),
                terminal=terminal,
                plan_complete=plan_complete,
            )
            if entry.summary is None or first_new_index < len(entry.deltas) or entry.summary_state != summary_state:
                technique_details_by_group = self._build_technique_details_by_group(
                    deltas=entry.deltas,
                    results=entry.results,
                )
                entry.summary = self._build_progress_summary(
                    plan=summary_plan,
                    plan_complete=plan_complete,
                    results=entry.results,
                    active_group_ids=active_group_ids,
                    terminal=terminal,
                    objective_scorer_identifier=objective_scorer_identifier,
                    technique_details_by_group=technique_details_by_group,
                )
                entry.summary_state = summary_state

            return ScenarioProgressSnapshot(
                deltas=tuple(entry.deltas),
                results=tuple(entry.results),
                summary=entry.summary,
                plan=summary_plan,
            )

    def _hydrate_new_deltas(self, *, scenario_result_id: str, entry: _ProgressCacheEntry) -> None:
        """Hydrate every persisted delta after the cached keyset cursor."""
        while True:
            page, has_more = self._memory.get_scenario_attack_result_deltas(
                scenario_result_id=scenario_result_id,
                cursor=entry.cursor,
                limit=self._STORAGE_PAGE_SIZE,
            )
            entry.deltas.extend(page)
            if page:
                last = page[-1]
                entry.cursor = AttackResultKeysetCursor(
                    timestamp=last.timestamp,
                    attack_result_id=last.attack_result_id,
                )
            if not has_more:
                return
            if not page:
                raise RuntimeError("Scenario progress storage returned an empty page with has_more=True.")

    @staticmethod
    def build_plan_lookup(*, plan: ScenarioRunPlan | None) -> ScenarioPlanLookup:
        """
        Build the shared lookup used for progress and legacy run summaries.

        Returns:
            ScenarioPlanLookup: Indexed plan data.
        """
        return ScenarioPlanLookup.from_plan(plan=plan)

    @staticmethod
    def resolve_result_unit_identity(
        *,
        atomic_attack_name: str,
        attack_result: AttackResult,
        plan_lookup: ScenarioPlanLookup,
    ) -> ResultUnitIdentity:
        """
        Resolve one attack attempt to its stable planned-unit identity.

        Returns:
            ResultUnitIdentity: The atomic-group and seed-group IDs.
        """
        return resolve_attack_result_attempt(
            atomic_attack_name=atomic_attack_name,
            attack_result=attack_result,
            plan_lookup=plan_lookup,
        ).unit

    @staticmethod
    def calculate_progress_counts(
        *,
        scenario_result: ScenarioResult,
        plan: ScenarioRunPlan | None,
        plan_lookup: ScenarioPlanLookup,
    ) -> tuple[int, int, int, int]:
        """
        Calculate planned-unit totals without inflating retries or error attempts.

        Delegates to ``pyrit.analytics.scenario_statistics`` so run details match the SDK and reports.

        Returns:
            tuple[int, int, int, int]: Total, completed, success-rate percentage,
                and successful-unit count.
        """
        overall = compute_scenario_statistics(scenario_result, plan=plan, use_saved_plan=False).overall
        total = overall.planned if overall.planned is not None else overall.completed
        return total, overall.completed, overall.success_percentage or 0, overall.succeeded

    @staticmethod
    def total_retry_pressure(*, attempts_per_unit: Iterable[int], persisted_retries: Iterable[int]) -> int:
        """
        Combine per-attempt retries with re-attempts of the same execution unit.

        Returns:
            int: Total retry pressure.
        """
        return retry_pressure(attempts_per_unit=attempts_per_unit, persisted_retries=persisted_retries)

    @staticmethod
    def _build_technique_details_by_group(
        *,
        deltas: Sequence[ScenarioAttackResultDelta],
        results: Sequence[ScenarioProgressResult],
    ) -> dict[str, ScenarioAttackTechniqueDetails]:
        """
        Build one technique-details projection for each enriched atomic group.

        Returns:
            dict[str, ScenarioAttackTechniqueDetails]: Details keyed by atomic group ID.
        """
        details_by_group: dict[str, ScenarioAttackTechniqueDetails] = {}
        for delta, result in zip(deltas, results, strict=True):
            atomic_identifier = delta.atomic_attack_identifier
            if (
                result.atomic_group_id in details_by_group
                or atomic_identifier is None
                or not atomic_identifier.seed_identifiers
                or atomic_identifier.attack_technique is None
            ):
                continue
            details_by_group[result.atomic_group_id] = ScenarioProgressReadModel._build_attack_technique_details(
                technique_identifier=atomic_identifier.attack_technique
            )
        return details_by_group

    @staticmethod
    def _build_progress_summary(
        *,
        plan: ScenarioRunPlan,
        plan_complete: bool,
        results: Sequence[ScenarioProgressResult],
        active_group_ids: Sequence[str],
        terminal: bool,
        objective_scorer_identifier: ComponentIdentifier | None,
        technique_details_by_group: dict[str, ScenarioAttackTechniqueDetails],
    ) -> ScenarioProgressSummary:
        """
        Build canonical progress rollups from a plan and persisted attempts.

        Returns:
            ScenarioProgressSummary: Progress grouped for client display.
        """
        attempts_by_unit: dict[ResultUnitIdentity, list[ScenarioProgressResult]] = {}
        for result in results:
            identity = ResultUnitIdentity(
                atomic_group_id=result.atomic_group_id,
                seed_group_id=result.seed_group_id,
            )
            attempts_by_unit.setdefault(identity, []).append(result)

        def aggregate(*, units: Sequence[ResultUnitIdentity], planned: int | None) -> ScenarioProgressCounts:
            return count_execution_units(units=units, attempts_by_unit=attempts_by_unit, planned=planned)

        group_units: dict[str, list[ResultUnitIdentity]] = {
            group.id: [
                ResultUnitIdentity(atomic_group_id=group.id, seed_group_id=seed_group_id)
                for seed_group_id in group.seed_group_ids
            ]
            for group in plan.atomic_groups
        }
        overall_units = (
            [unit for units in group_units.values() for unit in units] if plan_complete else list(attempts_by_unit)
        )
        overall = aggregate(
            units=overall_units,
            planned=len(overall_units) if plan_complete else None,
        )
        planned_units = set(overall_units)
        unattributed_attempts = sum(
            len(attempts) for unit, attempts in attempts_by_unit.items() if unit not in planned_units
        )
        if unattributed_attempts:
            logger.warning(
                "%d persisted attempt(s) matched no planned execution unit and are excluded from "
                "scenario progress rollups.",
                unattributed_attempts,
            )
        latest_results = [attempts_by_unit[unit][-1] for unit in overall_units if attempts_by_unit.get(unit)]
        objective_scorer = ScenarioProgressReadModel._build_objective_scorer(
            scorer_identifier=objective_scorer_identifier,
            results=latest_results,
        )

        active_ids = set(active_group_ids)
        atomic_groups: list[ScenarioAtomicGroupProgress] = []
        for group in plan.atomic_groups:
            units = group_units[group.id]
            counts = aggregate(
                units=units,
                planned=len(units) if plan_complete else None,
            )
            if not terminal and group.id in active_ids:
                group_status: Literal["RUNNING", "PENDING", "INCOMPLETE", "COMPLETED"] = "RUNNING"
            elif counts.planned is not None and counts.planned > 0 and counts.completed >= counts.planned:
                group_status = "COMPLETED"
            elif terminal:
                group_status = "INCOMPLETE"
            else:
                group_status = "PENDING"
            atomic_groups.append(
                ScenarioAtomicGroupProgress(
                    id=group.id,
                    atomic_attack_name=group.atomic_attack_name,
                    display_group=group.display_group,
                    status=group_status,
                    technique_details=technique_details_by_group.get(group.id),
                    **counts.model_dump(),
                )
            )
        status_order = {"RUNNING": 0, "PENDING": 1, "INCOMPLETE": 2, "COMPLETED": 3}
        atomic_groups.sort(
            key=lambda group: (
                status_order[group.status],
                group.display_group,
                group.atomic_attack_name,
            )
        )

        groups_by_technique: dict[str, list[ScenarioRunPlanAtomicGroup]] = {}
        groups_by_display: dict[str, list[ScenarioRunPlanAtomicGroup]] = {}
        for group in plan.atomic_groups:
            technique_name = group.technique_name or group.display_group
            groups_by_technique.setdefault(technique_name, []).append(group)
            groups_by_display.setdefault(group.display_group, []).append(group)
        display_groups: list[ScenarioDisplayGroupProgress] = []
        for display_group, groups in groups_by_display.items():
            units = [unit for group in groups for unit in group_units[group.id]]
            counts = aggregate(
                units=units,
                planned=len(units) if plan_complete else None,
            )
            display_groups.append(
                ScenarioDisplayGroupProgress(
                    id=display_group,
                    display_group=display_group,
                    atomic_attack_names=list(dict.fromkeys(group.atomic_attack_name for group in groups)),
                    atomic_group_ids=[group.id for group in groups],
                    **counts.model_dump(),
                )
            )
        display_groups.sort(key=lambda group: group.display_group)

        techniques: list[ScenarioTechniqueProgress] = []
        for technique_name, groups in groups_by_technique.items():
            units = [unit for group in groups for unit in group_units[group.id]]
            counts = aggregate(
                units=units,
                planned=len(units) if plan_complete else None,
            )
            descriptions = list(dict.fromkeys(group.description for group in groups if group.description))
            tags = sorted({tag for group in groups for tag in group.tags})
            techniques.append(
                ScenarioTechniqueProgress(
                    id=technique_name,
                    display_group=technique_name,
                    atomic_attack_names=list(dict.fromkeys(group.atomic_attack_name for group in groups)),
                    atomic_group_ids=[group.id for group in groups],
                    description=descriptions[0] if descriptions else None,
                    tags=tags,
                    **counts.model_dump(),
                )
            )
        techniques.sort(key=lambda technique: technique.display_group)

        seed_by_id = {seed.id: seed for seed in plan.seed_groups}
        seed_groups: list[ScenarioSeedGroupProgress] = []
        for seed_id, seed in seed_by_id.items():
            units = [
                ResultUnitIdentity(atomic_group_id=group.id, seed_group_id=seed_id)
                for group in plan.atomic_groups
                if seed_id in group.seed_group_ids
            ]
            counts = aggregate(
                units=units,
                planned=len(units) if plan_complete else None,
            )
            seed_groups.append(
                ScenarioSeedGroupProgress(
                    id=seed_id,
                    objective=seed.objective,
                    **counts.model_dump(),
                )
            )
        seed_groups.sort(key=lambda seed: seed.objective or seed.id)

        return ScenarioProgressSummary(
            overall=overall,
            objective_scorer=objective_scorer,
            display_groups=display_groups,
            techniques=techniques,
            seed_groups=seed_groups,
            atomic_groups=atomic_groups,
            unattributed_attempts=unattributed_attempts,
        )

    @staticmethod
    def _build_objective_scorer(
        *,
        scorer_identifier: ComponentIdentifier | None,
        results: Sequence[ScenarioProgressResult],
    ) -> ScenarioObjectiveScorer | None:
        """
        Build the objective scorer identity and its official evaluation metrics.

        Returns:
            ScenarioObjectiveScorer | None: Scorer information, or None when no scorer is known.
        """
        if scorer_identifier is None:
            scorer_names = {result.score.scorer_name for result in results if result.score is not None}
            if len(scorer_names) != 1:
                return None
            return ScenarioObjectiveScorer(component_name=next(iter(scorer_names)))

        official_metrics = find_objective_metrics_by_eval_hash(
            eval_hash=ScorerEvaluationIdentifier(scorer_identifier).eval_hash
        )
        metrics = (
            ScenarioObjectiveScorerMetrics(
                accuracy=official_metrics.accuracy,
                accuracy_standard_error=official_metrics.accuracy_standard_error,
                f1_score=official_metrics.f1_score,
                precision=official_metrics.precision,
                recall=official_metrics.recall,
                average_score_time_seconds=official_metrics.average_score_time_seconds,
            )
            if official_metrics
            else None
        )
        identity = ScenarioProgressReadModel._build_scorer_identity(scorer_identifier=scorer_identifier)
        return ScenarioObjectiveScorer(**identity.model_dump(), metrics=metrics)

    @staticmethod
    def _build_scorer_identity(*, scorer_identifier: ComponentIdentifier) -> ScenarioScorerIdentity:
        """
        Project the complete scorer identity used to distinguish configurations.

        Returns:
            ScenarioScorerIdentity: Scorer parameters and nested component identities.
        """
        projected = project_behavioral_identity(
            scorer_identifier,
            identifier_type=ScorerIdentifier,
        )
        identity = ScenarioProgressReadModel._build_component_identity(component_identifier=projected)
        return ScenarioScorerIdentity(
            component_name=identity.component_name,
            parameters=identity.parameters,
            children=identity.children,
        )

    @staticmethod
    def _build_component_identity(*, component_identifier: ComponentIdentifier) -> ScenarioComponentIdentity:
        """
        Project a component identifier without duplicating component-specific schemas.

        Returns:
            ScenarioComponentIdentity: Behavioral parameters and recursive child identities.
        """
        children: dict[str, list[ScenarioComponentIdentity]] = {}
        for child_name, child_value in component_identifier.children.items():
            child_identifiers = child_value if isinstance(child_value, list) else [child_value]
            children[child_name] = [
                ScenarioProgressReadModel._build_component_identity(component_identifier=child)
                for child in child_identifiers
            ]
        return ScenarioComponentIdentity(
            component_name=component_identifier.class_name,
            parameters=dict(component_identifier.params),
            children=children,
        )

    @staticmethod
    def _build_attack_technique_details(
        *,
        technique_identifier: ComponentIdentifier,
    ) -> ScenarioAttackTechniqueDetails:
        """
        Build REST details for an attack technique.

        Returns:
            ScenarioAttackTechniqueDetails: The projected technique details.
        """
        projected = project_behavioral_identity(
            technique_identifier,
            identifier_type=AttackTechniqueIdentifier,
        )
        details = ScenarioProgressReadModel._build_attack_technique_component_details(component_identifier=projected)
        return ScenarioAttackTechniqueDetails(
            component_name=details.component_name,
            parameters=details.parameters,
            children=details.children,
        )

    @staticmethod
    def _build_attack_technique_component_details(
        *,
        component_identifier: ComponentIdentifier,
    ) -> ScenarioComponentIdentity:
        """
        Map an already-projected technique component to its REST shape.

        Returns:
            ScenarioComponentIdentity: The mapped component details.
        """
        children: dict[str, list[ScenarioComponentIdentity]] = {}
        for child_name, child_value in component_identifier.children.items():
            child_identifiers = child_value if isinstance(child_value, list) else [child_value]
            if child_name == _TECHNIQUE_SEEDS_CHILD:
                children[child_name] = [
                    ScenarioProgressReadModel._build_technique_seed_details(seed_identifier=child)
                    for child in child_identifiers
                ]
            else:
                children[child_name] = [
                    ScenarioProgressReadModel._build_attack_technique_component_details(component_identifier=child)
                    for child in child_identifiers
                ]

        return ScenarioComponentIdentity(
            component_name=component_identifier.class_name,
            parameters=dict(component_identifier.params),
            children=children,
        )

    @staticmethod
    def _build_technique_seed_details(*, seed_identifier: ComponentIdentifier) -> ScenarioComponentIdentity:
        """
        Keep only seed content needed by the REST attack details.

        Returns:
            ScenarioComponentIdentity: The simplified seed details.
        """
        parameters = {
            name: seed_identifier.params[name]
            for name in _TECHNIQUE_SEED_DISPLAY_PARAMS
            if seed_identifier.params.get(name) is not None
        }
        return ScenarioComponentIdentity(
            component_name=seed_identifier.class_name,
            parameters=parameters,
        )

    @staticmethod
    def _map_progress_delta(
        *,
        delta: ScenarioAttackResultDelta,
        plan_lookup: ScenarioPlanLookup,
    ) -> ScenarioProgressResult:
        """
        Map a lightweight memory row to its REST progress representation.

        Returns:
            ScenarioProgressResult: The mapped progress delta.
        """
        atomic_attack_name = str(delta.attribution_data.get("parent_collection") or "")
        eval_hash = delta.attribution_data.get("parent_eval_hash")
        attributed_seed_group_id = delta.attribution_data.get("seed_group_id")
        unit = resolve_execution_unit(
            atomic_attack_name=atomic_attack_name,
            technique_eval_hash=str(eval_hash) if eval_hash is not None else None,
            attributed_seed_group_id=str(attributed_seed_group_id) if attributed_seed_group_id else None,
            atomic_attack_identifier=delta.atomic_attack_identifier,
            objective=delta.objective,
            objective_sha256=delta.objective_sha256,
            plan_lookup=plan_lookup,
        )
        atomic_group_id = unit.atomic_group_id
        seed_group_id = unit.seed_group_id
        return ScenarioProgressResult(
            attack_result_id=delta.attack_result_id,
            conversation_id=delta.conversation_id,
            atomic_group_id=atomic_group_id,
            atomic_attack_name=atomic_attack_name,
            seed_group_id=seed_group_id,
            outcome=delta.outcome,
            execution_time_ms=delta.execution_time_ms,
            timestamp=delta.timestamp,
            total_retries=delta.total_retries,
            retries=delta.retry_events,
            error_type=delta.error_type,
            error_message=delta.error_message,
            score=delta.score,
        )

    @staticmethod
    def _synthesize_legacy_plan(*, deltas: list[ScenarioAttackResultDelta]) -> ScenarioRunPlan:
        """
        Synthesize only known completed legacy units without claiming pending totals.

        Returns:
            ScenarioRunPlan: An incomplete plan containing only known units.
        """
        seeds: dict[str, ScenarioRunPlanSeedGroup] = {}
        groups: dict[str, ScenarioRunPlanAtomicGroup] = {}
        seen_seed_ids_by_group: dict[str, set[str]] = {}
        empty_plan_lookup = ScenarioPlanLookup.from_plan(plan=None)
        for delta in deltas:
            mapped = ScenarioProgressReadModel._map_progress_delta(
                delta=delta,
                plan_lookup=empty_plan_lookup,
            )
            seeds.setdefault(
                mapped.seed_group_id,
                ScenarioRunPlanSeedGroup(
                    id=mapped.seed_group_id,
                    objective_sha256=delta.objective_sha256 or to_sha256(delta.objective),
                    objective=delta.objective,
                ),
            )
            group = groups.setdefault(
                mapped.atomic_group_id,
                ScenarioRunPlanAtomicGroup(
                    id=mapped.atomic_group_id,
                    atomic_attack_name=mapped.atomic_attack_name,
                    display_group=mapped.atomic_attack_name,
                    technique_eval_hash=str(delta.attribution_data.get("parent_eval_hash") or ""),
                    seed_group_ids=[],
                ),
            )
            seen_seed_ids = seen_seed_ids_by_group.setdefault(mapped.atomic_group_id, set())
            if mapped.seed_group_id not in seen_seed_ids:
                seen_seed_ids.add(mapped.seed_group_id)
                group.seed_group_ids.append(mapped.seed_group_id)
        return ScenarioRunPlan(atomic_groups=list(groups.values()), seed_groups=list(seeds.values()))
