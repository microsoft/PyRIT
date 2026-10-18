# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from pyrit.analytics import compute_outcome_statistics
from pyrit.analytics.scenario_statistics import (
    ScenarioPlanLookup,
    combine_execution_counts,
    compute_scenario_statistics,
    resolve_execution_unit,
)
from pyrit.common.utils import to_sha256
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    ScenarioProgressCounts,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    config_hash,
)
from unit.mocks import make_scenario_result

_T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _result(*, objective: str, outcome: AttackOutcome, seconds: int = 0, **attribution: str) -> AttackResult:
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=objective,
        outcome=outcome,
        timestamp=_T0 + timedelta(seconds=seconds),
        attribution_data={"parent_collection": "attack", **attribution} if attribution else None,
    )


def _plan() -> ScenarioRunPlan:
    return ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="group",
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="eval",
                seed_group_ids=["seed-a", "seed-b"],
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(id="seed-a", objective_sha256=to_sha256("A"), objective="A"),
            ScenarioRunPlanSeedGroup(id="seed-b", objective_sha256=to_sha256("B"), objective="B"),
        ],
    )


def test_latest_attempt_decides_each_unit() -> None:
    result = make_scenario_result(
        attack_results={
            "attack": [
                _result(objective="A", outcome=AttackOutcome.SUCCESS, seconds=0),
                _result(objective="A", outcome=AttackOutcome.ERROR, seconds=1),
                _result(objective="B", outcome=AttackOutcome.ERROR, seconds=2),
                _result(objective="B", outcome=AttackOutcome.SUCCESS, seconds=3),
            ]
        }
    )

    statistics = compute_scenario_statistics(result)

    assert statistics.overall.completed == 2
    assert statistics.overall.succeeded == 1
    assert statistics.overall.success_percentage == 50
    assert statistics.overall.errors == 2
    assert statistics.attempts == 4


def test_empty_result_has_no_success_percentage() -> None:
    statistics = compute_scenario_statistics(make_scenario_result(attack_results={"attack": []}))

    assert statistics.overall.completed == 0
    assert statistics.overall.success_percentage is None
    assert statistics.attempts == 0
    assert statistics.overall.outcomes == compute_outcome_statistics({})


def test_latest_outcomes_share_both_denominators_without_historical_errors() -> None:
    result = make_scenario_result(
        attack_results={
            "one": [
                _result(objective="A", outcome=AttackOutcome.ERROR),
                _result(objective="A", outcome=AttackOutcome.SUCCESS, seconds=1),
                _result(objective="B", outcome=AttackOutcome.FAILURE),
            ],
            "two": [
                _result(objective="C", outcome=AttackOutcome.ERROR),
                _result(objective="C", outcome=AttackOutcome.ERROR, seconds=1),
                _result(objective="D", outcome=AttackOutcome.UNDETERMINED),
            ],
        },
        display_group_map={"one": "group", "two": "group"},
    )
    statistics = compute_scenario_statistics(result)
    expected = compute_outcome_statistics(dict.fromkeys(AttackOutcome, 1))
    assert statistics.overall.outcomes == expected
    assert statistics.display_groups["group"].outcomes == expected
    assert combine_execution_counts(statistics.atomic_attacks.values()) == statistics.overall
    assert statistics.overall.success_percentage == 25
    assert statistics.overall.outcomes.success_rate == 0.5
    assert statistics.overall.outcomes.success_rate_all == 0.25
    assert statistics.overall.errors == 3
    assert statistics.overall.outcomes.errors == 1
    assert statistics.overall.retries == 2


@pytest.mark.parametrize("outcome", list(AttackOutcome))
def test_each_latest_outcome_uses_shared_statistics(outcome: AttackOutcome) -> None:
    result = make_scenario_result(
        attack_results={
            "attack": [
                _result(objective="A", outcome=AttackOutcome.FAILURE),
                _result(objective="A", outcome=outcome, seconds=1),
            ]
        }
    )
    counts = compute_scenario_statistics(result).overall
    assert counts.outcomes == compute_outcome_statistics({outcome: 1})
    assert counts.completed == 1
    assert counts.success_percentage == (100 if outcome is AttackOutcome.SUCCESS else 0)


def test_combining_count_only_legacy_payloads_does_not_infer_failures_from_historical_errors(caplog) -> None:
    legacy = ScenarioProgressCounts(completed=2, succeeded=1, errors=5, retries=4, success_percentage=50)
    combined = combine_execution_counts([legacy])
    assert combined.outcomes is None
    assert combined.success_percentage == 50
    assert combined.errors == 5
    assert "without an outcome breakdown" in caplog.text


def test_combining_unequal_scenario_groups_recomputes_both_denominators() -> None:
    result = make_scenario_result(
        attack_results={
            "one": [_result(objective="A", outcome=AttackOutcome.SUCCESS)],
            "two": [
                _result(objective=str(index), outcome=outcome)
                for index, outcome in enumerate([AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.ERROR])
            ],
        }
    )
    statistics = compute_scenario_statistics(result)
    combined = combine_execution_counts(statistics.atomic_attacks.values())
    assert combined.outcomes.success_rate == pytest.approx(2 / 3)
    assert combined.outcomes.success_rate_all == 0.5
    assert combined == statistics.overall


@pytest.mark.parametrize("field", ["completed", "succeeded", "success_percentage"])
def test_combining_revalidates_mutated_scenario_totals(field: str) -> None:
    counts = ScenarioProgressCounts(
        completed=2,
        succeeded=1,
        errors=1,
        retries=0,
        success_percentage=50,
        outcomes=compute_outcome_statistics({"success": 1, "error": 1}),
    )
    setattr(counts, field, 10)
    with pytest.raises(ValueError, match="completed|succeeded|success_percentage"):
        combine_execution_counts([counts])


@pytest.mark.parametrize("field", ["successes", "total_results", "success_rate_all"])
def test_combining_revalidates_mutated_nested_outcomes(field: str) -> None:
    counts = ScenarioProgressCounts(
        completed=2,
        succeeded=1,
        errors=1,
        retries=0,
        outcomes=compute_outcome_statistics({"success": 1, "error": 1}),
    )
    setattr(counts.outcomes, field, 10)
    with pytest.raises(ValueError):
        combine_execution_counts([counts])


def test_saved_plan_counts_planned_units_and_reports_unattributed_attempts() -> None:
    result = make_scenario_result(
        attack_results={
            "attack": [
                _result(objective="A", outcome=AttackOutcome.SUCCESS, parent_eval_hash="eval", seed_group_id="seed-a"),
                _result(objective="Z", outcome=AttackOutcome.SUCCESS, parent_eval_hash="other-eval"),
            ]
        },
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: _plan().model_dump(mode="json")},
    )

    statistics = compute_scenario_statistics(result)

    assert statistics.overall.planned == 2
    assert statistics.overall.completed == 1
    assert statistics.overall.success_percentage == 100
    assert statistics.unattributed_attempts == 1
    assert statistics.display_groups["Attack"].planned == 2


def test_use_saved_plan_false_counts_a_legacy_run() -> None:
    result = make_scenario_result(
        attack_results={"attack": [_result(objective="A", outcome=AttackOutcome.SUCCESS)]},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: _plan().model_dump(mode="json")},
    )

    statistics = compute_scenario_statistics(result, use_saved_plan=False)

    assert statistics.overall.planned is None
    assert statistics.overall.completed == 1


def test_invalid_saved_plan_counts_as_legacy_run(caplog) -> None:
    result = make_scenario_result(
        attack_results={"attack": [_result(objective="A", outcome=AttackOutcome.SUCCESS)]},
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: {"atomic_groups": "invalid"}},
    )

    statistics = compute_scenario_statistics(result)

    assert statistics.overall.planned is None
    assert statistics.overall.success_percentage == 100
    assert "invalid saved run plan" in caplog.text


def test_resolve_execution_unit_precedence() -> None:
    lookup = ScenarioPlanLookup.from_plan(plan=_plan())

    def resolve(**kwargs):
        defaults = {
            "atomic_attack_name": "attack",
            "technique_eval_hash": "eval",
            "attributed_seed_group_id": None,
            "atomic_attack_identifier": None,
            "objective": "A",
            "objective_sha256": None,
            "plan_lookup": lookup,
        }
        return resolve_execution_unit(**{**defaults, **kwargs})

    # Attribution wins, then a unique objective match in the planned group, then an objective hash.
    assert resolve(attributed_seed_group_id="seed-b").seed_group_id == "seed-b"
    assert resolve().seed_group_id == "seed-a"
    assert resolve(objective="unplanned").seed_group_id == config_hash({"objective": "unplanned"})
    # Planned groups keep their plan ID; unplanned configurations get a name-and-hash identity.
    assert resolve().atomic_group_id == "group"
    assert resolve(technique_eval_hash="other").atomic_group_id == config_hash(
        {"atomic_attack_name": "attack", "technique_eval_hash": "other"}
    )
