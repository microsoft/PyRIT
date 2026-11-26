# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pyrit.analytics import compute_outcome_repeatability
from pyrit.common.utils import to_sha256
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanGroupKind,
    ScenarioRunPlanSeedGroup,
)
from unit.mocks import make_scenario_result

_TIME = datetime(2026, 9, 1, tzinfo=UTC)

S = AttackOutcome.SUCCESS
F = AttackOutcome.FAILURE
E = AttackOutcome.ERROR
U = AttackOutcome.UNDETERMINED


def _plan(
    attack_ids: tuple[str, ...] = ("a", "b", "c", "d"),
    baseline_ids: tuple[str, ...] = (),
) -> ScenarioRunPlan:
    groups = [
        ScenarioRunPlanAtomicGroup(
            id="attack",
            atomic_attack_name="attack",
            display_group="Attack",
            technique_eval_hash="attack-eval",
            technique_name="role_play",
            kind=ScenarioRunPlanGroupKind.ATTACK,
            seed_group_ids=list(attack_ids),
        )
    ]
    if baseline_ids:
        groups.append(
            ScenarioRunPlanAtomicGroup(
                id="base",
                atomic_attack_name="baseline",
                display_group="Baseline",
                technique_eval_hash="base-eval",
                kind=ScenarioRunPlanGroupKind.BASELINE,
                seed_group_ids=list(baseline_ids),
            )
        )
    return ScenarioRunPlan(
        atomic_groups=groups,
        seed_groups=[
            ScenarioRunPlanSeedGroup(id=seed, objective=seed, objective_sha256=to_sha256(seed))
            for seed in sorted(set(attack_ids) | set(baseline_ids))
        ],
    )


def _result(
    seed: str,
    outcome: AttackOutcome,
    *,
    group: str = "attack",
    seconds: int = 0,
    attempt_index: int | None = None,
) -> AttackResult:
    attribution: dict[str, object] = {"seed_group_id": seed, "parent_eval_hash": f"{group}-eval"}
    if attempt_index is not None:
        attribution["attempt_index"] = attempt_index
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        attack_result_id=str(uuid.uuid4()),
        objective=seed,
        outcome=outcome,
        timestamp=_TIME + timedelta(seconds=seconds),
        attribution_data=attribution,
    )


def _run(
    outcomes: dict[str, AttackOutcome], *, plan: ScenarioRunPlan | None = None, **identity: object
) -> ScenarioResult:
    plan = plan or _plan()
    return make_scenario_result(
        attack_results={"attack": [_result(seed, outcome) for seed, outcome in outcomes.items()]},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
        **identity,
    )


def test_counts_which_objectives_changed_even_when_run_rates_are_equal() -> None:
    report = compute_outcome_repeatability(
        [
            _run({"a": S, "b": F, "c": S, "d": F}),
            _run({"a": S, "b": F, "c": F, "d": S}),
        ]
    )

    counts = report.overall
    assert report.run_count == 2
    assert (counts.shared_units, counts.decided_units) == (4, 4)
    assert (counts.always_success, counts.always_failure, counts.mixed) == (1, 1, 2)
    assert counts.agreement_rate == 0.5
    assert counts.run_success_counts == (2, 2)
    assert counts.run_success_rates == (0.5, 0.5)
    assert counts.rate_range == 0.0


def test_compares_more_than_two_runs_in_the_order_given() -> None:
    report = compute_outcome_repeatability(
        [
            _run({"a": S, "b": S, "c": F, "d": F}),
            _run({"a": S, "b": S, "c": S, "d": F}),
            _run({"a": S, "b": F, "c": F, "d": F}),
        ]
    )

    counts = report.overall
    assert report.run_count == 3
    assert (counts.always_success, counts.always_failure, counts.mixed) == (1, 1, 2)
    assert counts.run_success_counts == (2, 3, 1)
    assert counts.run_success_rates == (0.5, 0.75, 0.25)
    assert counts.rate_range == 0.5


def test_keeps_errors_undetermined_and_missing_out_of_the_denominator_and_counts_them() -> None:
    report = compute_outcome_repeatability(
        [
            _run({"a": S, "b": E, "c": U, "d": E}),
            _run({"a": S, "b": F, "c": E}),
        ]
    )

    counts = report.overall
    assert (counts.shared_units, counts.decided_units) == (4, 1)
    assert (counts.always_success, counts.always_failure, counts.mixed) == (1, 0, 0)
    assert counts.agreement_rate == 1.0
    assert counts.run_success_rates == (1.0, 1.0)
    # "c" is undetermined in one run and errored in the other; "d" errored and is missing.
    assert (counts.missing_units, counts.error_units, counts.undetermined_units) == (1, 3, 1)


def test_reports_units_planned_in_only_some_runs_outside_the_shared_units() -> None:
    report = compute_outcome_repeatability(
        [
            _run({"a": S, "b": F}, plan=_plan(attack_ids=("a", "b"))),
            _run({"a": S, "b": F, "c": S}, plan=_plan(attack_ids=("a", "b", "c"))),
        ]
    )

    counts = report.overall
    assert (counts.shared_units, counts.decided_units, counts.partially_planned_units) == (2, 2, 1)
    assert counts.agreement_rate == 1.0


def test_uses_the_latest_top_level_completion_once_per_unit() -> None:
    first = _run({"a": S, "b": F, "c": S, "d": F})
    second = make_scenario_result(
        attack_results={
            "attack": [
                _result("a", F, seconds=0),
                _result("a", S, seconds=10),
                _result("b", S, seconds=20, attempt_index=1),
                _result("b", F, seconds=5),
                _result("c", S),
                _result("d", F),
            ]
        },
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: _plan().model_dump(mode="json")},
    )

    counts = compute_outcome_repeatability([first, second]).overall

    assert (counts.decided_units, counts.always_success, counts.always_failure, counts.mixed) == (4, 2, 2, 0)


def test_gives_the_same_counts_per_technique_label() -> None:
    plan = _plan(attack_ids=("a", "b"), baseline_ids=("a", "b"))

    def run(attack: tuple[AttackOutcome, AttackOutcome], baseline: tuple[AttackOutcome, AttackOutcome]):
        return make_scenario_result(
            attack_results={
                "attack": [_result("a", attack[0]), _result("b", attack[1])],
                "baseline": [_result("a", baseline[0], group="base"), _result("b", baseline[1], group="base")],
            },
            metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
        )

    report = compute_outcome_repeatability([run((S, S), (F, F)), run((S, F), (F, F))])

    assert set(report.by_technique) == {"baseline", "role_play"}
    assert (report.by_technique["role_play"].always_success, report.by_technique["role_play"].mixed) == (1, 1)
    assert report.by_technique["baseline"].always_failure == 2
    assert report.overall.decided_units == 4


def test_reports_no_rates_when_no_unit_is_decided_in_every_run() -> None:
    counts = compute_outcome_repeatability([_run({"a": E}), _run({"a": S})]).overall

    assert counts.decided_units == 0
    assert counts.agreement_rate is None
    assert counts.run_success_rates == (None, None)
    assert counts.rate_range is None


def test_requires_at_least_two_runs() -> None:
    with pytest.raises(ValueError, match="at least two"):
        compute_outcome_repeatability([_run({"a": S})])


def test_rejects_the_same_run_twice() -> None:
    run = _run({"a": S})
    with pytest.raises(ValueError, match="only once"):
        compute_outcome_repeatability([run, run])


def test_rejects_a_run_without_a_saved_plan() -> None:
    legacy = make_scenario_result(attack_results={"attack": [_result("a", S)]})
    with pytest.raises(ValueError, match="no valid saved run plan"):
        compute_outcome_repeatability([_run({"a": S}), legacy])


def test_rejects_runs_of_different_configurations() -> None:
    with pytest.raises(ValueError, match="same scenario configuration"):
        compute_outcome_repeatability([_run({"a": S}), _run({"a": S}, datasets=["other_dataset"])])
