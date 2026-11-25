# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Private reduction of persisted scenario attempts to scenario-history counters.

Memory loads each run's attempt columns with one indexed query and its run plan once, then resolves
attempts to logical work units here instead of expanding run plans inside SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models import AttackOutcome, AttackResultRole

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime

    from sqlalchemy.engine import RowMapping

    from pyrit.analytics.scenario_statistics import ScenarioPlanLookup


@dataclass(frozen=True, slots=True, kw_only=True)
class ScenarioHistoryAttempt:
    """The persisted columns of one scenario attempt that the history counters need."""

    scenario_result_id: str
    attempt_id: str
    atomic_attack_name: str
    technique_eval_hash: str
    attributed_seed_group_id: str | None
    # Unit seed identity without a plan: attributed seed group, identifier seed key, or objective hash.
    seed_group_id: str
    objective_sha256: str | None
    outcome: str
    timestamp: datetime
    total_retries: int
    has_retries: bool
    result_role: str | None
    is_completion: bool
    has_child_links: bool
    duplicates_child_error: bool

    @classmethod
    def from_row(cls, row: RowMapping) -> ScenarioHistoryAttempt:
        """
        Build an attempt from one row of the scenario-history attempt query.

        Returns:
            ScenarioHistoryAttempt: The typed attempt.
        """
        result_role = row["result_role"]
        return cls(
            scenario_result_id=str(row["scenario_result_id"]),
            attempt_id=str(row["attempt_id"]),
            atomic_attack_name=row["atomic_attack_name"],
            technique_eval_hash=row["technique_eval_hash"],
            attributed_seed_group_id=row["attributed_seed_group_id"],
            seed_group_id=row["seed_group_id"],
            objective_sha256=row["objective_sha256"],
            outcome=row["outcome"],
            timestamp=row["timestamp"],
            total_retries=row["total_retries"],
            has_retries=bool(row["has_retries"]),
            result_role=result_role if isinstance(result_role, str) else None,
            is_completion=row["attempt_index"] is None,
            has_child_links=bool(row["has_child_links"]),
            duplicates_child_error=bool(row["duplicates_child_error"]),
        )

    @property
    def is_error(self) -> bool:
        """Whether the attempt ended in an error."""
        return self.outcome == AttackOutcome.ERROR.value

    @property
    def is_envelope(self) -> bool:
        """Whether the attempt is an orchestration envelope; a missing or unrecognized role needs child links."""
        if self.result_role == AttackResultRole.ORCHESTRATION.value:
            return True
        if self.result_role == AttackResultRole.TARGET_FACING.value:
            return False
        return self.has_child_links


@dataclass(slots=True)
class ScenarioHistoryCounters:
    """Unit and attempt counters for one scenario run or one of its atomic groups."""

    unit_count: int = 0
    completed_units: int = 0
    successful_units: int = 0
    error_attempts: int = 0
    total_retries: int = 0
    outcome_successes: int = 0
    outcome_failures: int = 0
    outcome_errors: int = 0
    outcome_undetermined: int = 0
    raw_total_errors: int = 0
    raw_total_retries: int = 0
    raw_retrying_attacks_count: int = 0
    target_facing_attempts: int = 0
    target_facing_error_attempts: int = 0
    target_facing_retries: int = 0
    orchestration_attempts: int = 0
    orchestration_error_attempts: int = 0
    orchestration_retries: int = 0
    unknown_role_attempts: int = 0
    unknown_role_error_attempts: int = 0
    unknown_role_retries: int = 0
    latest_attempt_timestamp: datetime | None = None

    def add_unit(self, *, attempts: list[ScenarioHistoryAttempt], is_planned: bool) -> None:
        """
        Count one logical work unit by its latest attempt, plus every attempt's producer counters.

        Orchestration envelopes add pressure only when the unit has no other attempts or when
        they record an error that none of their linked children record.

        Args:
            attempts (list[ScenarioHistoryAttempt]): Every persisted attempt of the unit.
            is_planned (bool): Whether the unit counts toward unit, completion, and outcome counters.
        """
        has_execution_attempts = any(not attempt.is_envelope for attempt in attempts)
        pressure = [
            attempt
            for attempt in attempts
            if not has_execution_attempts
            or not attempt.is_envelope
            or (attempt.is_error and not attempt.duplicates_child_error)
        ]
        latest = max(attempts, key=lambda attempt: (attempt.is_completion, attempt.timestamp, attempt.attempt_id))
        self.raw_total_retries += max(sum(attempt.total_retries for attempt in attempts) + len(attempts) - 1, 0)
        if is_planned:
            self.unit_count += 1
            self.error_attempts += sum(1 for attempt in pressure if attempt.is_error)
            self.total_retries += max(sum(attempt.total_retries for attempt in pressure) + len(pressure) - 1, 0)
            if latest.is_completion:
                self._add_completed_outcome(outcome=latest.outcome)
        for attempt in attempts:
            self._add_attempt(attempt)

    def _add_completed_outcome(self, *, outcome: str) -> None:
        self.completed_units += 1
        if outcome == AttackOutcome.SUCCESS.value:
            self.successful_units += 1
            self.outcome_successes += 1
        elif outcome == AttackOutcome.FAILURE.value:
            self.outcome_failures += 1
        elif outcome == AttackOutcome.ERROR.value:
            self.outcome_errors += 1
        elif outcome == AttackOutcome.UNDETERMINED.value:
            self.outcome_undetermined += 1

    def _add_attempt(self, attempt: ScenarioHistoryAttempt) -> None:
        if self.latest_attempt_timestamp is None or attempt.timestamp > self.latest_attempt_timestamp:
            self.latest_attempt_timestamp = attempt.timestamp
        self.raw_total_errors += int(attempt.is_error)
        self.raw_retrying_attacks_count += int(attempt.has_retries)
        if attempt.result_role == AttackResultRole.TARGET_FACING.value:
            self.target_facing_attempts += 1
            self.target_facing_error_attempts += int(attempt.is_error)
            self.target_facing_retries += attempt.total_retries
        elif attempt.result_role == AttackResultRole.ORCHESTRATION.value:
            self.orchestration_attempts += 1
            self.orchestration_error_attempts += int(attempt.is_error)
            self.orchestration_retries += attempt.total_retries
        else:
            self.unknown_role_attempts += 1
            self.unknown_role_error_attempts += int(attempt.is_error)
            self.unknown_role_retries += attempt.total_retries


def aggregate_scenario_history_attempts(
    *,
    attempts: Iterable[ScenarioHistoryAttempt],
    plan_lookups: Mapping[str, ScenarioPlanLookup],
) -> tuple[dict[str, ScenarioHistoryCounters], dict[tuple[str, str], ScenarioHistoryCounters]]:
    """
    Group attempts into logical work units and count them per run and per atomic group.

    Args:
        attempts (Iterable[ScenarioHistoryAttempt]): Persisted attempts of the requested runs.
        plan_lookups (Mapping[str, ScenarioPlanLookup]): Run plans keyed by scenario result ID. Attempts
            of these runs that match no planned unit are left out of the unit counters.

    Returns:
        tuple[dict[str, ScenarioHistoryCounters], dict[tuple[str, str], ScenarioHistoryCounters]]:
            Counters by run ID, and counters by run ID and unit group ID.
    """
    units: dict[tuple[str, str, str], list[ScenarioHistoryAttempt]] = {}
    planned_units: set[tuple[str, str, str]] = set()
    for attempt in attempts:
        group_id, seed_id, is_planned = _resolve_unit(
            attempt=attempt, plan_lookup=plan_lookups.get(attempt.scenario_result_id)
        )
        unit = (attempt.scenario_result_id, group_id, seed_id)
        units.setdefault(unit, []).append(attempt)
        if is_planned:
            planned_units.add(unit)

    run_counters: dict[str, ScenarioHistoryCounters] = {}
    group_counters: dict[tuple[str, str], ScenarioHistoryCounters] = {}
    for unit, unit_attempts in units.items():
        run_id, group_id, _ = unit
        for counters in (
            run_counters.setdefault(run_id, ScenarioHistoryCounters()),
            group_counters.setdefault((run_id, group_id), ScenarioHistoryCounters()),
        ):
            counters.add_unit(attempts=unit_attempts, is_planned=unit in planned_units)
    return run_counters, dict(sorted(group_counters.items()))


def _resolve_unit(*, attempt: ScenarioHistoryAttempt, plan_lookup: ScenarioPlanLookup | None) -> tuple[str, str, bool]:
    """
    Resolve one attempt to its unit group ID, unit seed ID, and whether the unit counts.

    Without a matching planned unit, the unit is the atomic attack name plus technique configuration
    and the persisted seed identity; it counts only for runs resolved without a plan.

    Returns:
        tuple[str, str, bool]: The unit group ID, unit seed ID, and whether the unit counts.
    """
    unplanned = (
        f"{attempt.atomic_attack_name}\x1f{attempt.technique_eval_hash}",
        attempt.seed_group_id,
        plan_lookup is None,
    )
    if plan_lookup is None:
        return unplanned
    group = plan_lookup.resolve_group(
        atomic_attack_name=attempt.atomic_attack_name, technique_eval_hash=attempt.technique_eval_hash
    )
    if group is None:
        return unplanned
    if attempt.attributed_seed_group_id is not None:
        seed_ids: tuple[str, ...] = (
            (attempt.attributed_seed_group_id,) if attempt.attributed_seed_group_id in group.seed_group_ids else ()
        )
    elif attempt.objective_sha256 is not None:
        # Without explicit seed attribution, the objective must identify exactly one planned seed group.
        seed_ids = plan_lookup.seed_ids_by_group_and_objective.get((group.id, attempt.objective_sha256), ())
    else:
        seed_ids = ()
    if len(seed_ids) != 1:
        return unplanned
    return group.id, seed_ids[0], True
