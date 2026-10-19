# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Paired, effective baseline and attack outcomes within one planned scenario run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.analytics.scenario_statistics import (
    ScenarioPlanLookup,
    load_scenario_run_plan,
    resolve_attack_result_attempt,
)
from pyrit.common.utils import to_sha256
from pyrit.models import AttackOutcome, ScenarioExecutionUnit, ScenarioRunPlanGroupKind

if TYPE_CHECKING:
    from pyrit.analytics.scenario_statistics import ScenarioAttempt
    from pyrit.models import AttackResult, ScenarioResult, ScenarioRunPlan, ScenarioRunPlanAtomicGroup


@dataclass(frozen=True, slots=True)
class PairedOutcomeStatistics:
    """
    Transition counts and exclusions for one explicitly selected pair of planned groups.

    ``planned_pairs`` is the union of both groups' planned seed IDs. Only shared
    IDs whose latest top-level outcomes are both decided enter ``decided_pairs``.
    Error and undetermined counts describe each side, so they may overlap.
    """

    baseline_group_id: str
    attack_group_id: str
    planned_pairs: int
    decided_pairs: int
    excluded_pairs: int
    failure_to_success: int
    success_to_failure: int
    failure_to_failure: int
    success_to_success: int
    baseline_success_rate: float | None
    attack_success_rate: float | None
    paired_difference: float | None
    missing_baseline: int
    missing_attack: int
    missing_both: int
    baseline_errors: int
    attack_errors: int
    baseline_undetermined: int
    attack_undetermined: int
    unmatched_baseline_seeds: int
    unmatched_attack_seeds: int
    unattributed_attempts: int


def _selected_groups(
    *, scenario_result: ScenarioResult, baseline_group_id: str, attack_group_id: str
) -> tuple[ScenarioRunPlanAtomicGroup, ScenarioRunPlanAtomicGroup, ScenarioRunPlan, ScenarioPlanLookup]:
    """
    Validate the saved run plan and unambiguously resolve selected groups.

    Returns:
        tuple[ScenarioRunPlanAtomicGroup, ScenarioRunPlanAtomicGroup, ScenarioRunPlan, ScenarioPlanLookup]:
            The selected baseline, attack, validated plan and lookup.

    Raises:
        ValueError: If the plan or selected groups are invalid or ambiguous.
    """
    plan = load_scenario_run_plan(scenario_result)
    if plan is None:
        raise ValueError("Paired outcomes require a valid saved scenario run plan.")
    if baseline_group_id == attack_group_id:
        raise ValueError("Baseline and attack groups must be distinct.")

    by_id = {group.id: group for group in plan.atomic_groups}
    if baseline_group_id not in by_id or attack_group_id not in by_id:
        raise ValueError("Both selected atomic group IDs must exist in the saved run plan.")
    baseline, attack = by_id[baseline_group_id], by_id[attack_group_id]
    if baseline.kind != ScenarioRunPlanGroupKind.BASELINE or attack.kind != ScenarioRunPlanGroupKind.ATTACK:
        raise ValueError("Selected groups must have explicit baseline and attack run-plan kinds.")

    # The canonical resolver indexes by (atomic name, technique evaluation hash).
    # It cannot distinguish two planned groups with the same indexing identity.
    for group in (baseline, attack):
        if (
            sum(
                other.atomic_attack_name == group.atomic_attack_name
                and other.technique_eval_hash == group.technique_eval_hash
                for other in plan.atomic_groups
            )
            != 1
        ):
            raise ValueError("Selected atomic group has an ambiguous name/configuration identity.")

    return baseline, attack, plan, ScenarioPlanLookup.from_plan(plan=plan)


def _score_identity(result: AttackResult) -> object | None:
    """
    Use a persisted scorer identity when one is available.

    Returns:
        object | None: A persisted scorer identity, or None when absent.
    """
    score = result.last_score
    return score.scorer_class_identifier if score is not None else None


def compute_paired_outcome_statistics(
    scenario_result: ScenarioResult,
    *,
    baseline_group_id: str,
    attack_group_id: str,
) -> PairedOutcomeStatistics:
    """
    Compare one baseline and one attack group from the same planned scenario run.

    Match only identical logical seed IDs in the saved plan. The latest top-level
    attempt (timestamp, then result ID) determines each execution unit's outcome;
    child attempts and superseded retries do not become extra observations.
    Only pairs with SUCCESS/FAILURE on both sides enter the success rates.

    Args:
        scenario_result (ScenarioResult): Hydrated results with a saved normalized run plan.
        baseline_group_id (str): ID of a planned BASELINE atomic group.
        attack_group_id (str): ID of a planned ATTACK atomic group.

    Returns:
        PairedOutcomeStatistics: Observed transitions, paired rates, and exclusions.

    Raises:
        ValueError: If the run plan, selection, attribution, objectives, or available
            scorer identities cannot support an unambiguous comparison.
    """
    baseline, attack, plan, lookup = _selected_groups(
        scenario_result=scenario_result,
        baseline_group_id=baseline_group_id,
        attack_group_id=attack_group_id,
    )
    baseline_seeds, attack_seeds = set(baseline.seed_group_ids), set(attack.seed_group_ids)
    shared = baseline_seeds & attack_seeds
    if not shared and (baseline_seeds or attack_seeds):
        raise ValueError("Selected groups have no shared logical seed-group IDs to compare.")

    # The plan validates unique seed IDs. The same run carries the scenario-level
    # target/scorer identity; per-result scorer identities add a stronger check.
    objectives = {seed.id: seed.objective_sha256 for seed in plan.seed_groups}
    relevant_names = {baseline.atomic_attack_name, attack.atomic_attack_name}
    attempts_by_unit: dict[ScenarioExecutionUnit, list[tuple[ScenarioAttempt, AttackResult]]] = {}
    seen_ids: set[str] = set()
    unattributed_attempts = 0
    for name, results in scenario_result.attack_results.items():
        if name not in relevant_names:
            continue
        same_name_groups = lookup.groups_by_name.get(name, ())
        for result in results:
            attribution = result.attribution_data if isinstance(result.attribution_data, dict) else {}
            if len(same_name_groups) > 1 and not attribution.get("parent_eval_hash"):
                raise ValueError("Attempt attribution cannot distinguish multiple planned configurations.")
            attempt = resolve_attack_result_attempt(atomic_attack_name=name, attack_result=result, plan_lookup=lookup)
            if attempt.unit.atomic_group_id not in {baseline.id, attack.id}:
                unattributed_attempts += 1
                continue
            group = baseline if attempt.unit.atomic_group_id == baseline.id else attack
            if attempt.attempt_id in seen_ids:
                raise ValueError("Duplicate attack result IDs cannot be interpreted as separate attempts.")
            seen_ids.add(attempt.attempt_id)
            if attempt.unit.seed_group_id not in group.seed_group_ids:
                raise ValueError("An attempt refers to a seed group outside its planned atomic group.")
            if to_sha256(result.objective) != objectives[attempt.unit.seed_group_id]:
                raise ValueError("An attempt's objective disagrees with its planned logical seed group.")
            attempts_by_unit.setdefault(attempt.unit, []).append((attempt, result))

    effective: dict[ScenarioExecutionUnit, tuple[AttackOutcome, object | None]] = {}
    for unit, attempts in attempts_by_unit.items():
        attempts.sort(key=lambda entry: (entry[0].timestamp, entry[0].attempt_id))
        top_level = [entry for entry in attempts if entry[0].attempt_index is None]
        if top_level:
            attempt, result = top_level[-1]
            effective[unit] = (attempt.outcome, _score_identity(result))

    transitions: dict[tuple[AttackOutcome, AttackOutcome], int] = {
        (before, after): 0
        for before in (AttackOutcome.FAILURE, AttackOutcome.SUCCESS)
        for after in (AttackOutcome.FAILURE, AttackOutcome.SUCCESS)
    }
    missing_baseline = missing_attack = missing_both = 0
    baseline_errors = attack_errors = baseline_undetermined = attack_undetermined = 0

    for seed_id in shared:
        before = effective.get(ScenarioExecutionUnit(atomic_group_id=baseline.id, seed_group_id=seed_id))
        after = effective.get(ScenarioExecutionUnit(atomic_group_id=attack.id, seed_group_id=seed_id))
        if before is None and after is None:
            missing_both += 1
        elif before is None:
            missing_baseline += 1
        elif after is None:
            missing_attack += 1

        if before is not None:
            baseline_errors += before[0] == AttackOutcome.ERROR
            baseline_undetermined += before[0] == AttackOutcome.UNDETERMINED
        if after is not None:
            attack_errors += after[0] == AttackOutcome.ERROR
            attack_undetermined += after[0] == AttackOutcome.UNDETERMINED

        if before is None or after is None:
            continue
        if before[1] is not None and after[1] is not None and before[1] != after[1]:
            raise ValueError("Selected outcomes were evaluated by different scorers.")
        if (before[0], after[0]) in transitions:
            transitions[(before[0], after[0])] += 1

    decided = sum(transitions.values())
    baseline_successes = (
        transitions[(AttackOutcome.SUCCESS, AttackOutcome.FAILURE)]
        + transitions[(AttackOutcome.SUCCESS, AttackOutcome.SUCCESS)]
    )
    attack_successes = (
        transitions[(AttackOutcome.FAILURE, AttackOutcome.SUCCESS)]
        + transitions[(AttackOutcome.SUCCESS, AttackOutcome.SUCCESS)]
    )
    baseline_rate = baseline_successes / decided if decided else None
    attack_rate = attack_successes / decided if decided else None
    return PairedOutcomeStatistics(
        baseline_group_id=baseline.id,
        attack_group_id=attack.id,
        planned_pairs=len(baseline_seeds | attack_seeds),
        decided_pairs=decided,
        excluded_pairs=len(baseline_seeds | attack_seeds) - decided,
        failure_to_success=transitions[(AttackOutcome.FAILURE, AttackOutcome.SUCCESS)],
        success_to_failure=transitions[(AttackOutcome.SUCCESS, AttackOutcome.FAILURE)],
        failure_to_failure=transitions[(AttackOutcome.FAILURE, AttackOutcome.FAILURE)],
        success_to_success=transitions[(AttackOutcome.SUCCESS, AttackOutcome.SUCCESS)],
        baseline_success_rate=baseline_rate,
        attack_success_rate=attack_rate,
        paired_difference=attack_rate - baseline_rate
        if attack_rate is not None and baseline_rate is not None
        else None,
        missing_baseline=missing_baseline,
        missing_attack=missing_attack,
        missing_both=missing_both,
        baseline_errors=baseline_errors,
        attack_errors=attack_errors,
        baseline_undetermined=baseline_undetermined,
        attack_undetermined=attack_undetermined,
        unmatched_baseline_seeds=len(baseline_seeds - attack_seeds),
        unmatched_attack_seeds=len(attack_seeds - baseline_seeds),
        unattributed_attempts=unattributed_attempts,
    )
