# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Paired, effective baseline and attack outcomes within one planned scenario run."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from pyrit.analytics.scenario_statistics import (
    ScenarioPlanLookup,
    load_scenario_run_plan,
    resolve_attack_result_attempt,
)
from pyrit.common.utils import to_sha256
from pyrit.models import AttackOutcome, ScenarioExecutionUnit, ScenarioRunPlanGroupKind

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyrit.analytics.scenario_statistics import ScenarioAttempt
    from pyrit.models import AttackResult, ScenarioResult, ScenarioRunPlan, ScenarioRunPlanAtomicGroup
    from pyrit.models.score import ScoringExpectation


@dataclass(frozen=True, slots=True)
class PairedOutcomeStatistics:
    """
    Transition counts and exclusions for one explicitly selected pair of planned groups.

    ``planned_pairs`` counts seed IDs planned in both groups. Only shared IDs
    whose latest top-level outcomes are both decided enter ``decided_pairs``.
    Unmatched seed IDs are reported separately. Error and undetermined counts
    describe each side, so they may overlap.
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


@dataclass(frozen=True, slots=True)
class _ScoringEvidence:
    """Available scoring metadata for a persisted outcome."""

    scorer_identifier: object | None = None
    score_type: str | None = None
    expectation: ScoringExpectation | None = None


@dataclass(frozen=True, slots=True)
class _OutcomeEvidence:
    """Available evidence from a completion and its recorded child attempts."""

    scores: tuple[_ScoringEvidence, ...]
    objective_targets: tuple[str, ...]


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


def _score_details(result: AttackResult) -> _ScoringEvidence | None:
    """
    Extract available scorer identity, type, and scoring criteria.

    Returns:
        _ScoringEvidence | None: Persisted scorer identity, score type and expectation, if scored.
    """
    score = result.last_score
    if score is None:
        return None
    return _ScoringEvidence(
        scorer_identifier=score.scorer_class_identifier,
        score_type=score.score_type,
        expectation=score.scored_expectation,
    )


def _objective_target_hash(result: AttackResult) -> str | None:
    """
    Read the full recorded target identity, including model and endpoint.

    Returns:
        str | None: Target identity hash, if the result records an objective target.
    """
    identifier = result.atomic_attack_identifier
    if identifier is None:
        return None
    technique = identifier.get_child("attack_technique")
    attack = technique.get_child("attack") if technique is not None else identifier.get_child("attack")
    target = attack.get_child("objective_target") if attack is not None else None
    return target.hash if target is not None else None


def _linked_outcome_results(result: AttackResult, *, attempts_by_id: dict[str, AttackResult]) -> Iterator[AttackResult]:
    """
    Traverse explicitly linked descendants without recursion or duplicate evidence.

    Yields:
        AttackResult: The completion and its linked descendants.

    Raises:
        ValueError: If a linked child is unavailable or the links contain a cycle.
    """
    pending = [(result, False)]
    active: set[str] = set()
    visited: set[str] = set()
    while pending:
        linked, finished = pending.pop()
        result_id = linked.attack_result_id
        if finished:
            active.remove(result_id)
            visited.add(result_id)
            continue
        if result_id in active:
            raise ValueError("A selected compound outcome contains a cycle in its child attack results.")
        if result_id in visited:
            continue
        active.add(result_id)
        pending.append((linked, True))
        child_ids = getattr(linked, "child_attack_result_ids", linked.metadata.get("child_attack_result_ids", ()))
        live_children = {child.attack_result_id: child for child in getattr(linked, "child_attack_results", ())}
        for child_id in reversed(child_ids):
            child = attempts_by_id.get(child_id) or live_children.get(child_id)
            if child is None:
                raise ValueError("A selected compound outcome references a missing child attack result.")
            pending.append((child, False))
        yield linked


def _outcome_evidence(result: AttackResult, *, attempts_by_id: dict[str, AttackResult]) -> _OutcomeEvidence:
    """
    Retain scorer and target evidence from a completion and its linked descendants.

    Unrelated children and superseded top-level retries cannot affect it.

    Returns:
        _OutcomeEvidence: Available scoring and objective-target identities.

    Raises:
        ValueError: If linked descendants are missing or contain a cycle.
    """
    scores: list[_ScoringEvidence] = []
    objective_targets: list[str] = []
    for linked in _linked_outcome_results(result, attempts_by_id=attempts_by_id):
        score = _score_details(linked)
        if score is not None:
            scores.append(score)
        target = _objective_target_hash(linked)
        if target is not None:
            objective_targets.append(target)
    return _OutcomeEvidence(scores=tuple(scores), objective_targets=tuple(objective_targets))


def _check_pair_evidence(before: _OutcomeEvidence, after: _OutcomeEvidence) -> None:
    """
    Reject known target and scoring incompatibilities within one seed pair.

    Raises:
        ValueError: If recorded target or scoring evidence conflicts.
    """
    targets = before.objective_targets + after.objective_targets
    if targets and any(target != targets[0] for target in targets[1:]):
        raise ValueError("Selected outcomes used different objective targets.")

    scores = before.scores + after.scores
    for attribute, message in (
        ("scorer_identifier", "Selected outcomes were evaluated by different scorers."),
        ("score_type", "Selected outcomes have incompatible score types."),
        ("expectation", "Selected outcomes have incompatible scoring expectations."),
    ):
        values = [value for score in scores if (value := getattr(score, attribute)) is not None]
        if values and any(value != values[0] for value in values[1:]):
            raise ValueError(message)


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
    # target/scorer identity; per-result identities add stronger checks.
    objectives = {seed.id: seed.objective_sha256 for seed in plan.seed_groups}
    relevant_names = {baseline.atomic_attack_name, attack.atomic_attack_name}
    selected_ids = {baseline.id, attack.id}
    attempts_by_unit: dict[ScenarioExecutionUnit, list[tuple[ScenarioAttempt, AttackResult]]] = {}
    seen_ids: set[str] = set()
    unattributed_attempts = 0
    for name, results in scenario_result.attack_results.items():
        if name not in relevant_names:
            continue
        same_name_groups = lookup.groups_by_name.get(name, ())
        for result in results:
            if result.attribution_data is not None and not isinstance(result.attribution_data, dict):
                raise ValueError("An attempt has invalid attribution metadata.")
            attribution = result.attribution_data or {}
            eval_hash = attribution.get("parent_eval_hash")
            if isinstance(eval_hash, str):
                known_group = lookup.groups_by_identity.get((name, eval_hash))
                if known_group is not None and known_group.id not in selected_ids:
                    unattributed_attempts += 1
                    continue
            recorded_name = attribution.get("parent_collection")
            if recorded_name is not None and recorded_name != name:
                raise ValueError("An attempt's parent_collection disagrees with its recorded atomic name.")
            if not isinstance(result.outcome, AttackOutcome):
                raise ValueError("An attempt has an invalid outcome.")
            if (
                not isinstance(result.timestamp, datetime)
                or result.timestamp.tzinfo is None
                or result.timestamp.utcoffset() is None
            ):
                raise ValueError("An attempt has an invalid timestamp.")
            if not isinstance(result.metadata, dict):
                raise ValueError("An attempt has invalid result metadata.")
            children = result.metadata.get("child_attack_result_ids", ())
            if not isinstance(children, (list, tuple)) or any(not isinstance(child, str) for child in children):
                raise ValueError("An attempt has invalid child_attack_result_ids metadata.")
            child_index = attribution.get("attempt_index")
            if child_index is not None and (
                not isinstance(child_index, int) or isinstance(child_index, bool) or child_index < 1
            ):
                raise ValueError("An attempt has an invalid attempt_index.")
            if len(same_name_groups) > 1 and not attribution.get("parent_eval_hash"):
                raise ValueError("Attempt attribution cannot distinguish multiple planned configurations.")
            attempt = resolve_attack_result_attempt(atomic_attack_name=name, attack_result=result, plan_lookup=lookup)
            if attempt.unit.atomic_group_id not in selected_ids:
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

    effective: dict[ScenarioExecutionUnit, tuple[AttackOutcome, AttackResult, dict[str, AttackResult]]] = {}
    for unit, attempts in attempts_by_unit.items():
        attempts.sort(key=lambda entry: (entry[0].timestamp, entry[0].attempt_id))
        top_level = [entry for entry in attempts if entry[0].attempt_index is None]
        if top_level:
            attempt, result = top_level[-1]
            by_id = {candidate.attack_result_id: candidate for _, candidate in attempts}
            effective[unit] = (attempt.outcome, result, by_id)

    transitions: dict[tuple[AttackOutcome, AttackOutcome], int] = {
        (before, after): 0
        for before in (AttackOutcome.FAILURE, AttackOutcome.SUCCESS)
        for after in (AttackOutcome.FAILURE, AttackOutcome.SUCCESS)
    }
    missing_baseline = missing_attack = missing_both = 0
    baseline_errors = attack_errors = baseline_undetermined = attack_undetermined = 0
    observed_scorer: object | None = None
    observed_score_type: str | None = None
    observed_target: str | None = None

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
        before_score = _outcome_evidence(before[1], attempts_by_id=before[2])
        after_score = _outcome_evidence(after[1], attempts_by_id=after[2])
        _check_pair_evidence(before_score, after_score)
        if (before[0], after[0]) in transitions:
            for target in before_score.objective_targets + after_score.objective_targets:
                if observed_target is not None and target != observed_target:
                    raise ValueError("Decided pairs contain incompatible objective targets.")
                observed_target = target
            for scoring_evidence in before_score.scores + after_score.scores:
                scorer, score_type = scoring_evidence.scorer_identifier, scoring_evidence.score_type
                if scorer is not None:
                    if observed_scorer is not None and scorer != observed_scorer:
                        raise ValueError("Decided pairs contain incompatible scorer identities.")
                    observed_scorer = scorer
                if score_type is not None:
                    if observed_score_type is not None and score_type != observed_score_type:
                        raise ValueError("Decided pairs contain incompatible score types.")
                    observed_score_type = score_type
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
        planned_pairs=len(shared),
        decided_pairs=decided,
        excluded_pairs=len(shared) - decided,
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
