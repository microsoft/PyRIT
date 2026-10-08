# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime, timedelta

from pyrit.analytics.scenario_statistics import (
    ScenarioPlanLookup,
    compute_scenario_statistics,
    resolve_execution_unit,
)
from pyrit.common.utils import to_sha256
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
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
