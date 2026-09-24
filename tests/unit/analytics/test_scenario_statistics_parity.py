# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Parity coverage for scenario success statistics.

The same saved history must produce identical effective-unit statistics through the SDK
(``compute_scenario_statistics``), the GUI API (run detail, run history list, and live progress),
and the reports (JSON printer).
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from pyrit.analytics import compute_scenario_statistics
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.common.utils import to_sha256
from pyrit.memory import MemoryInterface
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunState,
)
from pyrit.output.scenario_result.json import JsonScenarioResultPrinter
from unit.mocks import make_scenario_result

_T0 = datetime(2026, 9, 1, tzinfo=UTC)


@dataclass(frozen=True)
class _Attempt:
    atomic_attack_name: str
    objective: str
    outcome: AttackOutcome
    eval_hash: str | None = "eval"
    seed_group_id: str | None = None


@dataclass(frozen=True)
class _History:
    attempts: list[_Attempt]
    plan: ScenarioRunPlan | None = None
    display_group_map: dict[str, str] = field(default_factory=dict)


def _group(*, name: str, eval_hash: str, seed_ids: list[str], display_group: str | None = None):
    return ScenarioRunPlanAtomicGroup(
        id=f"{name}-{eval_hash}",
        atomic_attack_name=name,
        display_group=display_group or name,
        technique_eval_hash=eval_hash,
        seed_group_ids=seed_ids,
    )


def _seed(seed_id: str, objective: str) -> ScenarioRunPlanSeedGroup:
    return ScenarioRunPlanSeedGroup(id=seed_id, objective_sha256=to_sha256(objective), objective=objective)


def _plan(*groups: ScenarioRunPlanAtomicGroup, seeds: list[ScenarioRunPlanSeedGroup]) -> ScenarioRunPlan:
    return ScenarioRunPlan(scenario_registry_name="test.scenario", atomic_groups=list(groups), seed_groups=seeds)


_HISTORIES = {
    "retry_and_resume_recovered": _History(
        plan=_plan(
            _group(name="attack", eval_hash="eval", seed_ids=["a", "b"]), seeds=[_seed("a", "A"), _seed("b", "B")]
        ),
        attempts=[
            _Attempt("attack", "A", AttackOutcome.SUCCESS, seed_group_id="a"),
            _Attempt("attack", "B", AttackOutcome.ERROR, seed_group_id="b"),
            _Attempt("attack", "B", AttackOutcome.ERROR, seed_group_id="b"),
            _Attempt("attack", "B", AttackOutcome.SUCCESS, seed_group_id="b"),
        ],
    ),
    "unrecovered_errors": _History(
        plan=_plan(
            _group(name="attack", eval_hash="eval", seed_ids=["a", "b"]), seeds=[_seed("a", "A"), _seed("b", "B")]
        ),
        attempts=[
            _Attempt("attack", "A", AttackOutcome.SUCCESS, seed_group_id="a"),
            _Attempt("attack", "B", AttackOutcome.ERROR, seed_group_id="b"),
            _Attempt("attack", "B", AttackOutcome.ERROR, seed_group_id="b"),
        ],
    ),
    "legacy_identities_without_plan": _History(
        attempts=[
            _Attempt("attack", "A", AttackOutcome.SUCCESS, eval_hash=None),
            _Attempt("attack", "B", AttackOutcome.ERROR, eval_hash=None),
            _Attempt("attack", "B", AttackOutcome.SUCCESS, eval_hash=None),
            _Attempt("other", "A", AttackOutcome.FAILURE, eval_hash=None),
        ],
    ),
    "legacy_error_matched_by_saved_plan": _History(
        plan=_plan(_group(name="attack", eval_hash="eval", seed_ids=["a"]), seeds=[_seed("a", "A")]),
        attempts=[
            # An older error row without seed attribution resolves to the planned unit by objective.
            _Attempt("attack", "A", AttackOutcome.ERROR),
            _Attempt("attack", "A", AttackOutcome.SUCCESS, seed_group_id="a"),
        ],
    ),
    "technique_configurations_sharing_a_name": _History(
        plan=_plan(
            _group(name="attack", eval_hash="eval-1", seed_ids=["a"], display_group="Attack"),
            _group(name="attack", eval_hash="eval-2", seed_ids=["a"], display_group="Attack"),
            seeds=[_seed("a", "A")],
        ),
        attempts=[
            _Attempt("attack", "A", AttackOutcome.SUCCESS, eval_hash="eval-1", seed_group_id="a"),
            _Attempt("attack", "A", AttackOutcome.FAILURE, eval_hash="eval-2", seed_group_id="a"),
        ],
    ),
    "display_groups": _History(
        plan=_plan(
            _group(name="base64", eval_hash="e1", seed_ids=["a", "b"], display_group="encoding"),
            _group(name="rot13", eval_hash="e2", seed_ids=["a", "b"], display_group="encoding"),
            _group(name="crescendo", eval_hash="e3", seed_ids=["a"], display_group="multi_turn"),
            seeds=[_seed("a", "A"), _seed("b", "B")],
        ),
        display_group_map={"base64": "encoding", "rot13": "encoding", "crescendo": "multi_turn"},
        attempts=[
            _Attempt("base64", "A", AttackOutcome.SUCCESS, eval_hash="e1", seed_group_id="a"),
            _Attempt("base64", "B", AttackOutcome.FAILURE, eval_hash="e1", seed_group_id="b"),
            _Attempt("rot13", "A", AttackOutcome.ERROR, eval_hash="e2", seed_group_id="a"),
            _Attempt("rot13", "A", AttackOutcome.SUCCESS, eval_hash="e2", seed_group_id="a"),
            _Attempt("crescendo", "A", AttackOutcome.UNDETERMINED, eval_hash="e3", seed_group_id="a"),
        ],
    ),
    "empty_history": _History(
        plan=_plan(_group(name="attack", eval_hash="eval", seed_ids=["a"]), seeds=[_seed("a", "A")]),
        attempts=[],
    ),
}

# Effective-unit success percentages each history must report everywhere (None: no completed unit).
_EXPECTED_OVERALL = {
    "retry_and_resume_recovered": 100,
    "unrecovered_errors": 50,
    "legacy_identities_without_plan": 66,
    "legacy_error_matched_by_saved_plan": 100,
    "technique_configurations_sharing_a_name": 50,
    "display_groups": 50,
    "empty_history": None,
}


def _persist(memory: MemoryInterface, history: _History) -> str:
    scenario_result_id = uuid.uuid4()
    metadata = {SCENARIO_RUN_PLAN_METADATA_KEY: history.plan.model_dump(mode="json")} if history.plan else {}
    scenario_result = make_scenario_result(
        id=scenario_result_id,
        scenario_name="ParityScenario",
        objective_target_identifier=ComponentIdentifier(class_name="MockTarget", class_module="tests"),
        scenario_run_state=ScenarioRunState.COMPLETED,
        attack_results={},
        creation_time=_T0,
        display_group_map=history.display_group_map,
        metadata=metadata,
    )
    memory.add_scenario_results_to_memory(scenario_results=[scenario_result])
    attack_results = []
    for index, attempt in enumerate(history.attempts):
        attribution_data: dict[str, str] = {"parent_collection": attempt.atomic_attack_name}
        if attempt.eval_hash is not None:
            attribution_data["parent_eval_hash"] = attempt.eval_hash
        if attempt.seed_group_id is not None:
            attribution_data["seed_group_id"] = attempt.seed_group_id
        attack_results.append(
            AttackResult(
                conversation_id=f"conversation-{index}",
                objective=attempt.objective,
                outcome=attempt.outcome,
                timestamp=_T0 + timedelta(seconds=index),
                attribution_parent_id=str(scenario_result_id),
                attribution_data=attribution_data,
            )
        )
    if attack_results:
        memory.add_attack_results_to_memory(attack_results=attack_results)
    return str(scenario_result_id)


@pytest.mark.parametrize("history_name", sorted(_HISTORIES))
async def test_sdk_api_and_reports_report_identical_statistics(history_name: str, sqlite_instance) -> None:
    history = _HISTORIES[history_name]
    scenario_result_id = _persist(sqlite_instance, history)
    expected = _EXPECTED_OVERALL[history_name]

    # SDK
    [scenario_result] = sqlite_instance.get_scenario_results(scenario_result_ids=[scenario_result_id])
    sdk = compute_scenario_statistics(scenario_result)
    assert sdk.overall.success_percentage == expected

    # API: run detail, history list (SQL aggregate), and live progress
    service = ScenarioRunService()
    detail = service.get_run_from_storage(scenario_result_id=scenario_result_id, active_error=None)
    [list_item] = [item for item in service.list_runs().items if item.scenario_result_id == scenario_result_id]
    progress = service.get_run_progress_from_storage(
        scenario_result_id=scenario_result_id, since=None, limit=500, active_group_ids=[]
    )
    assert detail is not None
    assert progress is not None
    assert detail.objective_achieved_rate == (expected or 0)
    assert list_item.objective_achieved_rate == (expected or 0)
    assert progress.summary.overall.success_percentage == expected
    assert detail.completed_attacks == sdk.overall.completed == progress.summary.overall.completed
    assert list_item.completed_attacks == sdk.overall.completed
    assert progress.summary.overall.succeeded == sdk.overall.succeeded
    assert progress.summary.overall.errors == sdk.overall.errors

    # Reports
    report = json.loads(await JsonScenarioResultPrinter().render_async(scenario_result))
    assert report["stats"]["overall_success_rate"] == (expected or 0)

    # Per-group numbers agree between the saved-plan progress view and the reports.
    if history.plan is not None:
        progress_groups = {group.display_group: group.success_percentage for group in progress.summary.display_groups}
        sdk_groups = {name: counts.success_percentage for name, counts in sdk.display_groups.items()}
        assert sdk_groups == progress_groups
        report_groups = {group["name"]: group["success_rate"] for group in report["groups"]}
        for name, rate in report_groups.items():
            assert rate == (progress_groups.get(name) or 0)


def test_historical_attempt_counts_stay_separate_from_units(sqlite_instance) -> None:
    scenario_result_id = _persist(sqlite_instance, _HISTORIES["retry_and_resume_recovered"])
    [scenario_result] = sqlite_instance.get_scenario_results(scenario_result_ids=[scenario_result_id])

    statistics = compute_scenario_statistics(scenario_result)

    assert statistics.attempts == 4
    assert statistics.overall.completed == 2
    assert statistics.overall.planned == 2
    assert statistics.overall.errors == 2
    assert statistics.overall.retries == 2
    assert statistics.unattributed_attempts == 0
