# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Effective execution-unit statistics for scenario runs.

This is the single implementation of scenario success statistics. The SDK (``ScenarioResult``), the
GUI backend, and the console, JSON, and HTML reports all derive their numbers from it, so they cannot
drift apart. It owns:

- execution-unit identity: an atomic group (atomic attack name plus technique configuration) and a
  logical seed group, resolved against the saved run plan when one exists;
- attempt selection: each unit counts once, by its latest attempt (timestamp, then attempt ID);
- counts, denominators, and rounding: the success percentage is succeeded units over completed units,
  truncated to an integer.

Historical attempt, error, and retry counts are reported separately from the effective-unit counts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from pyrit.common.utils import to_sha256
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ScenarioExecutionStatistics,
    ScenarioExecutionUnit,
    ScenarioProgressCounts,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    config_hash,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from pyrit.models import ScenarioResult

logger = logging.getLogger(__name__)

#: Default for ``compute_scenario_statistics(plan=...)``: use the plan saved in the result's metadata.
SAVED_RUN_PLAN: Any = object()


@dataclass(frozen=True, slots=True)
class ScenarioPlanLookup:
    """Pre-indexed run-plan data used while resolving persisted attempts to execution units."""

    groups_by_identity: dict[tuple[str, str], ScenarioRunPlanAtomicGroup]
    groups_by_name: dict[str, tuple[ScenarioRunPlanAtomicGroup, ...]]
    seed_ids_by_group_and_objective: dict[tuple[str, str], tuple[str, ...]]
    planned_units: frozenset[ScenarioExecutionUnit]

    @classmethod
    def from_plan(cls, *, plan: ScenarioRunPlan | None) -> ScenarioPlanLookup:
        """
        Build constant-time lookup tables for one run plan.

        Returns:
            ScenarioPlanLookup: Indexed plan data.
        """
        if plan is None:
            return cls(
                groups_by_identity={},
                groups_by_name={},
                seed_ids_by_group_and_objective={},
                planned_units=frozenset(),
            )

        groups_by_identity: dict[tuple[str, str], ScenarioRunPlanAtomicGroup] = {}
        grouped_by_name: dict[str, list[ScenarioRunPlanAtomicGroup]] = {}
        seeds_by_id = {seed.id: seed for seed in plan.seed_groups}
        seed_ids_by_group_and_objective: dict[tuple[str, str], tuple[str, ...]] = {}
        planned_units: set[ScenarioExecutionUnit] = set()
        for group in plan.atomic_groups:
            groups_by_identity[(group.atomic_attack_name, group.technique_eval_hash)] = group
            grouped_by_name.setdefault(group.atomic_attack_name, []).append(group)
            seed_ids_by_objective: dict[str, list[str]] = {}
            for seed_id in group.seed_group_ids:
                seed = seeds_by_id[seed_id]
                seed_ids_by_objective.setdefault(seed.objective_sha256, []).append(seed_id)
            seed_ids_by_group_and_objective.update(
                {
                    (group.id, objective_sha256): tuple(seed_ids)
                    for objective_sha256, seed_ids in seed_ids_by_objective.items()
                }
            )
            planned_units.update(
                ScenarioExecutionUnit(atomic_group_id=group.id, seed_group_id=seed_group_id)
                for seed_group_id in group.seed_group_ids
            )

        return cls(
            groups_by_identity=groups_by_identity,
            groups_by_name={name: tuple(groups) for name, groups in grouped_by_name.items()},
            seed_ids_by_group_and_objective=seed_ids_by_group_and_objective,
            planned_units=frozenset(planned_units),
        )

    def resolve_group(
        self,
        *,
        atomic_attack_name: str,
        technique_eval_hash: str | None,
    ) -> ScenarioRunPlanAtomicGroup | None:
        """
        Resolve one planned group from persisted attribution.

        Returns:
            ScenarioRunPlanAtomicGroup | None: The uniquely matching group.
        """
        if technique_eval_hash is not None:
            return self.groups_by_identity.get((atomic_attack_name, technique_eval_hash))
        matching_groups = self.groups_by_name.get(atomic_attack_name, ())
        return matching_groups[0] if len(matching_groups) == 1 else None


@dataclass(frozen=True, slots=True)
class ScenarioAttempt:
    """One persisted attempt, resolved to the execution unit it belongs to."""

    unit: ScenarioExecutionUnit
    atomic_attack_name: str
    outcome: AttackOutcome
    timestamp: datetime
    attempt_id: str
    total_retries: int


def load_scenario_run_plan(scenario_result: ScenarioResult) -> ScenarioRunPlan | None:
    """
    Load the run plan saved in a scenario result's metadata.

    Returns:
        ScenarioRunPlan | None: The saved plan, or None for results persisted without one.
    """
    raw_plan = (scenario_result.metadata or {}).get(SCENARIO_RUN_PLAN_METADATA_KEY)
    if raw_plan is None:
        return None
    try:
        return ScenarioRunPlan.model_validate(raw_plan)
    except (ValidationError, ValueError):
        logger.warning(
            "Scenario result %s has an invalid saved run plan; counting it as a legacy run.", scenario_result.id
        )
        return None


def resolve_execution_unit(
    *,
    atomic_attack_name: str,
    technique_eval_hash: str | None,
    attributed_seed_group_id: str | None,
    atomic_attack_identifier: AtomicAttackIdentifier | None,
    objective: str,
    objective_sha256: str | None,
    plan_lookup: ScenarioPlanLookup,
) -> ScenarioExecutionUnit:
    """
    Resolve one persisted attempt to its execution unit.

    The atomic group is the planned group matching the atomic attack name and technique configuration,
    or a hash of both for attempts the plan does not describe. The seed group comes from, in order: the
    persisted attribution, the atomic identifier's logical seed group, a unique objective match in the
    planned group, and finally a hash of the objective (legacy rows).

    Returns:
        ScenarioExecutionUnit: The resolved execution unit.
    """
    atomic_group_id = config_hash(
        {"atomic_attack_name": atomic_attack_name, "technique_eval_hash": technique_eval_hash or ""}
    )
    planned_group = plan_lookup.resolve_group(
        atomic_attack_name=atomic_attack_name,
        technique_eval_hash=technique_eval_hash,
    )
    if planned_group is not None:
        atomic_group_id = planned_group.id

    seed_group_id = attributed_seed_group_id or ""
    if not seed_group_id and atomic_attack_identifier is not None and atomic_attack_identifier.seed_identifiers:
        seed_group_id = atomic_attack_identifier.logical_seed_group_id
    if not seed_group_id:
        matching_seed_ids = plan_lookup.seed_ids_by_group_and_objective.get(
            (atomic_group_id, objective_sha256 or to_sha256(objective)),
            (),
        )
        if len(matching_seed_ids) == 1:
            seed_group_id = matching_seed_ids[0]
    if not seed_group_id:
        seed_group_id = config_hash({"objective": objective})
    return ScenarioExecutionUnit(atomic_group_id=atomic_group_id, seed_group_id=seed_group_id)


def resolve_attack_result_attempt(
    *,
    atomic_attack_name: str,
    attack_result: AttackResult,
    plan_lookup: ScenarioPlanLookup,
) -> ScenarioAttempt:
    """
    Resolve one hydrated attack result to a scenario attempt.

    Returns:
        ScenarioAttempt: The attempt and its execution unit.
    """
    attribution_data = attack_result.attribution_data if isinstance(attack_result.attribution_data, dict) else {}
    eval_hash = attribution_data.get("parent_eval_hash")
    attributed_seed_group_id = attribution_data.get("seed_group_id")
    atomic_identifier = attack_result.atomic_attack_identifier
    typed_identifier = (
        AtomicAttackIdentifier.from_component_identifier(atomic_identifier)
        if isinstance(atomic_identifier, ComponentIdentifier)
        else None
    )
    # Hydrated rows (and test doubles) may carry a non-integer retry count; treat it as zero.
    retries = getattr(attack_result, "total_retries", 0)
    return ScenarioAttempt(
        unit=resolve_execution_unit(
            atomic_attack_name=atomic_attack_name,
            technique_eval_hash=str(eval_hash) if eval_hash is not None else None,
            attributed_seed_group_id=str(attributed_seed_group_id) if attributed_seed_group_id else None,
            atomic_attack_identifier=typed_identifier,
            objective=str(attack_result.objective),
            objective_sha256=None,
            plan_lookup=plan_lookup,
        ),
        atomic_attack_name=atomic_attack_name,
        outcome=attack_result.outcome,
        timestamp=_timestamp_order_key(attack_result.timestamp),
        attempt_id=str(attack_result.attack_result_id),
        total_retries=retries if isinstance(retries, int) else 0,
    )


def retry_pressure(*, attempts_per_unit: Iterable[int], persisted_retries: Iterable[int]) -> int:
    """
    Combine per-attempt retries with re-attempts of the same execution unit.

    Returns:
        int: Total retry pressure.
    """
    within_attempts = sum(max(0, retries) for retries in persisted_retries)
    repeated_units = sum(max(0, count - 1) for count in attempts_per_unit)
    return within_attempts + repeated_units


def success_percentage(*, succeeded: int, completed: int) -> int | None:
    """
    Return the success percentage for effective execution units.

    Returns:
        int | None: ``succeeded / completed`` as a truncated integer percentage, or None with no
            completed units.
    """
    return int((succeeded / completed) * 100) if completed else None


def count_execution_units(
    *,
    units: Iterable[ScenarioExecutionUnit],
    attempts_by_unit: Mapping[ScenarioExecutionUnit, Sequence[Any]],
    planned: int | None,
) -> ScenarioProgressCounts:
    """
    Count effective execution units from chronologically ordered attempts.

    Each item in ``attempts_by_unit`` must expose ``outcome`` and ``total_retries`` and be ordered
    oldest first; the last attempt decides the unit's outcome.

    Returns:
        ScenarioProgressCounts: Completed and succeeded units plus historical errors and retries.
    """
    completed = 0
    succeeded = 0
    errors = 0
    retries = 0
    for unit in units:
        attempts = attempts_by_unit.get(unit, ())
        if not attempts:
            continue
        completed += 1
        succeeded += int(attempts[-1].outcome == AttackOutcome.SUCCESS)
        errors += sum(int(attempt.outcome == AttackOutcome.ERROR) for attempt in attempts)
        retries += retry_pressure(
            attempts_per_unit=[len(attempts)],
            persisted_retries=[attempt.total_retries for attempt in attempts],
        )
    return ScenarioProgressCounts(
        completed=completed,
        planned=planned,
        succeeded=succeeded,
        success_percentage=success_percentage(succeeded=succeeded, completed=completed),
        errors=errors,
        retries=retries,
    )


def compute_scenario_statistics(
    scenario_result: ScenarioResult,
    *,
    plan: ScenarioRunPlan | None = SAVED_RUN_PLAN,
) -> ScenarioExecutionStatistics:
    """
    Calculate effective execution-unit statistics for a scenario result.

    With a run plan, counts cover the planned units, and attempts that match no planned unit are
    reported as unattributed. Without one (legacy results), every resolved unit counts and display
    groups come from ``display_group_map``.

    Args:
        scenario_result (ScenarioResult): The scenario result with its hydrated attack results.
        plan (ScenarioRunPlan | None): The run plan, or None to count the result as a legacy run.
            Defaults to the plan saved in the result's metadata.

    Returns:
        ScenarioExecutionStatistics: Overall, per atomic attack, and per display group counts.
    """
    if plan is SAVED_RUN_PLAN:
        plan = load_scenario_run_plan(scenario_result)
    plan_lookup = ScenarioPlanLookup.from_plan(plan=plan)

    attempts = [
        resolve_attack_result_attempt(
            atomic_attack_name=atomic_attack_name,
            attack_result=attack_result,
            plan_lookup=plan_lookup,
        )
        for atomic_attack_name, results in scenario_result.attack_results.items()
        for attack_result in results
    ]
    attempts.sort(key=lambda attempt: (attempt.timestamp, attempt.attempt_id))
    attempts_by_unit: dict[ScenarioExecutionUnit, list[ScenarioAttempt]] = {}
    for attempt in attempts:
        attempts_by_unit.setdefault(attempt.unit, []).append(attempt)

    units_by_name: dict[str, list[ScenarioExecutionUnit]] = {}
    units_by_display_group: dict[str, list[ScenarioExecutionUnit]] = {}
    if plan is not None:
        for group in plan.atomic_groups:
            group_units = [
                ScenarioExecutionUnit(atomic_group_id=group.id, seed_group_id=seed_group_id)
                for seed_group_id in group.seed_group_ids
            ]
            units_by_name.setdefault(group.atomic_attack_name, []).extend(group_units)
            units_by_display_group.setdefault(group.display_group, []).extend(group_units)
        counted_units = list(dict.fromkeys(unit for units in units_by_name.values() for unit in units))
        planned: int | None = len(counted_units)
    else:
        for unit, unit_attempts in attempts_by_unit.items():
            name = unit_attempts[0].atomic_attack_name
            units_by_name.setdefault(name, []).append(unit)
            display_group = scenario_result.display_group_map.get(name, name)
            units_by_display_group.setdefault(display_group, []).append(unit)
        counted_units = list(attempts_by_unit)
        planned = None

    counted = set(counted_units)
    unattributed_attempts = sum(
        len(unit_attempts) for unit, unit_attempts in attempts_by_unit.items() if unit not in counted
    )

    def _count(units: Sequence[ScenarioExecutionUnit]) -> ScenarioProgressCounts:
        return count_execution_units(
            units=units,
            attempts_by_unit=attempts_by_unit,
            planned=len(units) if planned is not None else None,
        )

    return ScenarioExecutionStatistics(
        overall=_count(counted_units),
        atomic_attacks={name: _count(units) for name, units in units_by_name.items()},
        display_groups={name: _count(units) for name, units in units_by_display_group.items()},
        attempts=len(attempts),
        unattributed_attempts=unattributed_attempts,
    )


def _timestamp_order_key(timestamp: object) -> datetime:
    """
    Normalize potentially malformed timestamps from mutable result objects.

    Returns:
        datetime: The timestamp or a stable earliest-time fallback.
    """
    return timestamp if isinstance(timestamp, datetime) else datetime.min.replace(tzinfo=UTC)
