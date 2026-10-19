# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pyrit.analytics.paired_outcome_statistics import compute_paired_outcome_statistics
from pyrit.common.utils import to_sha256
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanGroupKind,
    ScenarioRunPlanSeedGroup,
    Score,
)
from unit.mocks import make_scenario_result

_TIME = datetime(2026, 9, 1, tzinfo=UTC)


def _plan(
    baseline_ids: tuple[str, ...] = ("a", "b", "c", "d"),
    attack_ids: tuple[str, ...] = ("a", "b", "c", "d"),
) -> ScenarioRunPlan:
    return ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="base",
                atomic_attack_name="baseline",
                display_group="Baseline",
                technique_eval_hash="base-eval",
                kind=ScenarioRunPlanGroupKind.BASELINE,
                seed_group_ids=list(baseline_ids),
            ),
            ScenarioRunPlanAtomicGroup(
                id="attack",
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="attack-eval",
                kind=ScenarioRunPlanGroupKind.ATTACK,
                seed_group_ids=list(attack_ids),
            ),
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(id=seed, objective=seed, objective_sha256=to_sha256(seed))
            for seed in sorted(set(baseline_ids) | set(attack_ids))
        ],
    )


def _result(
    seed: str,
    outcome: AttackOutcome,
    *,
    seconds: int = 0,
    group: str | None = None,
    attempt_index: int | None = None,
    result_id: str | None = None,
    objective: str | None = None,
) -> AttackResult:
    attribution = {"seed_group_id": seed}
    if group is not None:
        attribution["parent_eval_hash"] = f"{group}-eval"
    if attempt_index is not None:
        attribution["attempt_index"] = attempt_index
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        attack_result_id=result_id or str(uuid.uuid4()),
        objective=objective or seed,
        outcome=outcome,
        timestamp=_TIME + timedelta(seconds=seconds),
        attribution_data=attribution,
    )


def _statistics(plan: ScenarioRunPlan, attack_results: dict[str, list[AttackResult]]):
    scenario = make_scenario_result(
        attack_results=attack_results,
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
    )
    return compute_paired_outcome_statistics(scenario, baseline_group_id="base", attack_group_id="attack")


def test_paired_outcomes_show_transitions_even_when_marginal_rates_are_equal() -> None:
    plan = _plan()
    result = _statistics(
        plan,
        {
            "baseline": [
                _result("a", AttackOutcome.SUCCESS),
                _result("b", AttackOutcome.SUCCESS),
                _result("c", AttackOutcome.FAILURE),
                _result("d", AttackOutcome.FAILURE),
            ],
            "attack": [
                _result("a", AttackOutcome.SUCCESS),
                _result("b", AttackOutcome.FAILURE),
                _result("c", AttackOutcome.SUCCESS),
                _result("d", AttackOutcome.FAILURE),
            ],
        },
    )

    assert (result.failure_to_success, result.success_to_failure) == (1, 1)
    assert (result.failure_to_failure, result.success_to_success) == (1, 1)
    assert (result.planned_pairs, result.decided_pairs, result.excluded_pairs) == (4, 4, 0)
    assert result.baseline_success_rate == result.attack_success_rate == 0.5
    assert result.paired_difference == 0.0


def test_paired_outcomes_use_latest_top_level_completion_once() -> None:
    plan = _plan(("a",), ("a",))
    stats = _statistics(
        plan,
        {
            "baseline": [_result("a", AttackOutcome.FAILURE)],
            "attack": [
                _result("a", AttackOutcome.FAILURE, seconds=-3),
                _result("a", AttackOutcome.ERROR, seconds=-2),
                _result("a", AttackOutcome.SUCCESS, seconds=0),
                _result("a", AttackOutcome.FAILURE, seconds=2, attempt_index=1),
            ],
        },
    )

    assert stats.failure_to_success == 1
    assert stats.decided_pairs == 1
    assert stats.attack_errors == 0
    assert stats.paired_difference == 1.0


def test_paired_outcomes_account_for_exclusions_without_rate_denominator_drift() -> None:
    stats = _statistics(
        _plan(("a", "b", "c", "d"), ("a", "b", "c", "e")),
        {
            "baseline": [
                _result("a", AttackOutcome.ERROR),
                _result("b", AttackOutcome.UNDETERMINED),
                _result("c", AttackOutcome.FAILURE),
            ],
            "attack": [
                _result("a", AttackOutcome.SUCCESS),
                _result("b", AttackOutcome.ERROR),
                _result("e", AttackOutcome.SUCCESS),
            ],
        },
    )

    assert (stats.planned_pairs, stats.decided_pairs, stats.excluded_pairs) == (5, 0, 5)
    assert (stats.unmatched_baseline_seeds, stats.unmatched_attack_seeds) == (1, 1)
    assert (stats.missing_baseline, stats.missing_attack, stats.missing_both) == (0, 1, 0)
    assert (stats.baseline_errors, stats.attack_errors) == (1, 1)
    assert (stats.baseline_undetermined, stats.attack_undetermined) == (1, 0)
    assert stats.baseline_success_rate is stats.attack_success_rate is stats.paired_difference is None


def test_paired_outcomes_account_for_both_missing_and_one_sided_missing() -> None:
    stats = _statistics(
        _plan(("a", "b", "c"), ("a", "b", "c")),
        {"baseline": [_result("b", AttackOutcome.SUCCESS)], "attack": [_result("c", AttackOutcome.FAILURE)]},
    )

    assert stats.missing_both == 1
    assert stats.missing_attack == 1
    assert stats.missing_baseline == 1
    assert stats.excluded_pairs == 3


def test_paired_outcomes_allow_empty_planned_groups() -> None:
    stats = _statistics(_plan((), ()), {})
    assert stats.planned_pairs == stats.decided_pairs == stats.excluded_pairs == 0
    assert stats.paired_difference is None
    assert stats.baseline_success_rate is None


def test_paired_outcomes_reject_disjoint_seed_groups() -> None:
    with pytest.raises(ValueError, match="no shared logical seed-group"):
        _statistics(_plan(("a",), ("b",)), {})


def test_paired_outcomes_require_valid_plan_and_explicit_kinds() -> None:
    scenario = make_scenario_result(attack_results={})
    with pytest.raises(ValueError, match="valid saved scenario run plan"):
        compute_paired_outcome_statistics(scenario, baseline_group_id="base", attack_group_id="attack")

    plan = _plan(("a",), ("a",))
    plan.atomic_groups[0].kind = None
    with pytest.raises(ValueError, match="explicit baseline and attack"):
        _statistics(plan, {})


def test_paired_outcomes_reject_ambiguous_group_configuration() -> None:
    plan = _plan(("a",), ("a",))
    plan.atomic_groups.append(
        ScenarioRunPlanAtomicGroup(
            id="duplicate",
            atomic_attack_name="baseline",
            display_group="Other",
            technique_eval_hash="base-eval",
            kind=ScenarioRunPlanGroupKind.BASELINE,
            seed_group_ids=["a"],
        )
    )
    with pytest.raises(ValueError, match="ambiguous name/configuration"):
        _statistics(plan, {})


def test_paired_outcomes_reject_duplicate_results() -> None:
    duplicate = _result("a", AttackOutcome.SUCCESS)
    with pytest.raises(ValueError, match="Duplicate attack result IDs"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [duplicate, duplicate]})


def test_paired_outcomes_reject_objective_drift_in_attributed_result() -> None:
    with pytest.raises(ValueError, match="objective disagrees"):
        _statistics(
            _plan(("a",), ("a",)),
            {"attack": [_result("a", AttackOutcome.SUCCESS, objective="a different objective")]},
        )


def test_paired_outcomes_reject_ambiguous_attempt_attribution() -> None:
    plan = _plan(("a",), ("a",))
    plan.atomic_groups.append(
        ScenarioRunPlanAtomicGroup(
            id="other",
            atomic_attack_name="attack",
            display_group="Another config",
            technique_eval_hash="other-eval",
            kind=ScenarioRunPlanGroupKind.ATTACK,
            seed_group_ids=["a"],
        )
    )
    with pytest.raises(ValueError, match="cannot distinguish multiple planned configurations"):
        _statistics(plan, {"attack": [_result("a", AttackOutcome.SUCCESS)]})


def _score(scorer: str) -> Score:
    return Score(
        score_type="true_false",
        score_value="true",
        scorer_class_identifier=ComponentIdentifier(class_name=scorer, class_module="tests"),
    )


def test_paired_outcomes_reject_incompatible_persisted_scorers() -> None:
    baseline = _result("a", AttackOutcome.SUCCESS, group="base")
    attack = _result("a", AttackOutcome.SUCCESS, group="attack")
    baseline.automated_score = _score("ScorerA")
    attack.automated_score = _score("ScorerB")

    with pytest.raises(ValueError, match="different scorers"):
        _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [attack]})


def test_paired_outcomes_use_human_override_score_identity() -> None:
    baseline = _result("a", AttackOutcome.FAILURE, group="base")
    attack = _result("a", AttackOutcome.SUCCESS, group="attack")
    baseline.automated_score = _score("ScorerA")
    attack.automated_score = _score("OldScorer")
    attack.human_score = _score("ScorerA")
    stats = _statistics(_plan(("a",), ("a",)), {"baseline": [baseline], "attack": [attack]})

    assert stats.failure_to_success == 1


def test_paired_outcomes_include_reused_persisted_completion() -> None:
    # Reused completions can precede the live scenario and still carry valid
    # plan attribution. Their timestamp must not prevent matching.
    cached_baseline = _result("a", AttackOutcome.FAILURE, seconds=-3600, group="base")
    cached_attack = _result("a", AttackOutcome.SUCCESS, seconds=-3600, group="attack")
    stats = _statistics(
        _plan(("a",), ("a",)),
        {"baseline": [cached_baseline], "attack": [cached_attack]},
    )

    assert (stats.planned_pairs, stats.decided_pairs, stats.failure_to_success) == (1, 1, 1)
