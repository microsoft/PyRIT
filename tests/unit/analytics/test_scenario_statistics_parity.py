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
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    ComponentIdentifier,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunState,
    SeedObjective,
    SeedPrompt,
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
    # Prompt context carried only by the atomic identifier's seeds, like legacy rows without seed attribution.
    seed_context: str | None = None
    # Attribute the attempt to the logical seed group made of the objective and this prompt context.
    attributed_seed_context: str | None = None


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
    "legacy_technique_configurations_sharing_a_name": _History(
        # No saved plan, same atomic attack name and seed, different technique configurations: two units.
        attempts=[
            _Attempt("attack", "A", AttackOutcome.ERROR, eval_hash="config-a"),
            _Attempt("attack", "A", AttackOutcome.SUCCESS, eval_hash="config-b"),
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
        display_group_map={"attack": "Attack"},
        attempts=[
            _Attempt("attack", "A", AttackOutcome.SUCCESS, eval_hash="eval-1", seed_group_id="a"),
            _Attempt("attack", "A", AttackOutcome.FAILURE, eval_hash="eval-2", seed_group_id="a"),
        ],
    ),
    "legacy_attempt_with_ambiguous_name": _History(
        # A row with no technique hash can't be told apart when two planned groups share its name, so it stays
        # unattributed everywhere instead of being guessed onto the first group.
        plan=_plan(
            _group(name="attack", eval_hash="eval-1", seed_ids=["a"]),
            _group(name="attack", eval_hash="eval-2", seed_ids=["a"]),
            seeds=[_seed("a", "A")],
        ),
        attempts=[
            _Attempt("attack", "A", AttackOutcome.FAILURE, eval_hash="eval-1", seed_group_id="a"),
            _Attempt("attack", "A", AttackOutcome.SUCCESS, eval_hash=None, seed_group_id="a"),
        ],
    ),
    "legacy_seed_groups_sharing_an_objective": _History(
        # No saved plan or seed attribution; the atomic identifiers' seeds tell the two seed groups apart,
        # so these are two units (one recovered from an error), not one unit retried.
        attempts=[
            _Attempt("attack", "A", AttackOutcome.FAILURE, seed_context="context one"),
            _Attempt("attack", "A", AttackOutcome.ERROR, seed_context="context two"),
            _Attempt("attack", "A", AttackOutcome.SUCCESS, seed_context="context two"),
        ],
    ),
    "identifier_and_attributed_rows_of_one_seed_group": _History(
        # One logical seed group recorded three ways: an older row with only the identifier's seeds, a row with
        # only the attributed seed group ID, and a row with both. All three are attempts of the same unit.
        attempts=[
            _Attempt("attack", "A", AttackOutcome.ERROR, seed_context="context"),
            _Attempt("attack", "A", AttackOutcome.ERROR, attributed_seed_context="context"),
            _Attempt("attack", "A", AttackOutcome.SUCCESS, seed_context="context", attributed_seed_context="context"),
        ],
    ),
    "explicit_attribution_wins_over_identifier_seeds": _History(
        # Same stored identifier, different explicitly attributed seed groups (like benchmark cache copies): two units.
        attempts=[
            _Attempt("attack", "A", AttackOutcome.FAILURE, seed_context="old", attributed_seed_context="first"),
            _Attempt("attack", "A", AttackOutcome.SUCCESS, seed_context="old", attributed_seed_context="second"),
        ],
    ),
    "one_attributed_seed_group_with_two_identifiers": _History(
        # The explicit attribution decides the unit even when the stored identifiers differ: one recovered unit.
        attempts=[
            _Attempt("attack", "A", AttackOutcome.FAILURE, seed_context="one", attributed_seed_context="context"),
            _Attempt("attack", "A", AttackOutcome.SUCCESS, seed_context="two", attributed_seed_context="context"),
        ],
    ),
    "identifier_only_then_attributed_only": _History(
        # No row carries both forms, yet both name the same logical seed group: one recovered unit.
        attempts=[
            _Attempt("attack", "A", AttackOutcome.ERROR, seed_context="context"),
            _Attempt("attack", "A", AttackOutcome.SUCCESS, attributed_seed_context="context"),
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
    "legacy_technique_configurations_sharing_a_name": 50,
    "legacy_error_matched_by_saved_plan": 100,
    "technique_configurations_sharing_a_name": 50,
    "legacy_attempt_with_ambiguous_name": 0,
    "legacy_seed_groups_sharing_an_objective": 50,
    "identifier_and_attributed_rows_of_one_seed_group": 100,
    "explicit_attribution_wins_over_identifier_seeds": 50,
    "one_attributed_seed_group_with_two_identifiers": 100,
    "identifier_only_then_attributed_only": 100,
    "display_groups": 50,
    "empty_history": None,
}


def _seed_group(objective: str, context: str) -> AttackSeedGroup:
    return AttackSeedGroup(seeds=[SeedObjective(value=objective), SeedPrompt(value=context)])


async def _persist(memory: MemoryInterface, history: _History) -> str:
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
    await memory.add_scenario_results_to_memory_async(scenario_results=[scenario_result])
    attack_results = []
    for index, attempt in enumerate(history.attempts):
        attribution_data: dict[str, str] = {"parent_collection": attempt.atomic_attack_name}
        if attempt.eval_hash is not None:
            attribution_data["parent_eval_hash"] = attempt.eval_hash
        if attempt.seed_group_id is not None:
            attribution_data["seed_group_id"] = attempt.seed_group_id
        if attempt.attributed_seed_context is not None:
            seed_group = _seed_group(attempt.objective, attempt.attributed_seed_context)
            attribution_data["seed_group_id"] = seed_group.logical_id
        atomic_attack_identifier = None
        if attempt.seed_context is not None:
            atomic_attack_identifier = AtomicAttackIdentifier.build(
                attack_identifier=ComponentIdentifier(class_name="MockAttack", class_module="tests"),
                seed_group=_seed_group(attempt.objective, attempt.seed_context),
            )
        attack_results.append(
            AttackResult(
                conversation_id=f"conversation-{index}",
                objective=attempt.objective,
                outcome=attempt.outcome,
                timestamp=_T0 + timedelta(seconds=index),
                attribution_parent_id=str(scenario_result_id),
                attribution_data=attribution_data,
                atomic_attack_identifier=atomic_attack_identifier,
            )
        )
    if attack_results:
        await memory.add_attack_results_to_memory_async(attack_results=attack_results)
    return str(scenario_result_id)


@pytest.mark.parametrize("history_name", sorted(_HISTORIES))
async def test_sdk_api_and_reports_report_identical_statistics(history_name: str, sqlite_instance) -> None:
    history = _HISTORIES[history_name]
    scenario_result_id = await _persist(sqlite_instance, history)
    expected = _EXPECTED_OVERALL[history_name]

    # SDK
    [scenario_result] = await sqlite_instance.get_scenario_results_async(scenario_result_ids=[scenario_result_id])
    sdk = compute_scenario_statistics(scenario_result)
    assert sdk.overall.success_percentage == expected

    # API: run detail, history list (SQL aggregate), and live progress
    service = ScenarioRunService()
    detail = await service.get_run_from_storage_async(scenario_result_id=scenario_result_id, active_error=None)
    runs = await service.list_runs_async()
    [list_item] = [item for item in runs.items if item.scenario_result_id == scenario_result_id]
    progress = await service.get_run_progress_from_storage_async(
        scenario_result_id=scenario_result_id, since=None, limit=500, active_group_ids=[]
    )
    assert detail is not None
    assert progress is not None
    assert detail.objective_achieved_rate == (expected or 0)
    assert list_item.objective_achieved_rate == (expected or 0)
    assert progress.summary.overall.success_percentage == expected
    assert detail.completed_attacks == sdk.overall.completed == progress.summary.overall.completed
    assert list_item.completed_attacks == sdk.overall.completed
    assert list_item.total_retries == detail.total_retries == sdk.overall.retries
    assert progress.summary.overall.succeeded == sdk.overall.succeeded
    assert progress.summary.overall.errors == sdk.overall.errors

    # Reports
    report = json.loads(await JsonScenarioResultPrinter().render_async(scenario_result))
    assert report["stats"]["overall_success_rate"] == (expected or 0)

    # Per-group numbers agree between the SDK, the saved-plan progress view, and the reports. Compare
    # key sets first so a group missing from one view fails instead of reading as 0%.
    sdk_groups = {name: (counts.completed, counts.success_percentage) for name, counts in sdk.display_groups.items()}
    report_groups = {
        group["name"]: (group["num_objective_executions"], group["success_rate"])
        for group in report["groups"]
        if group["num_attempts"]
    }
    # Reports list the groups that have results; planned groups with no attempts only appear in the SDK/API.
    sdk_groups_with_results = {name: value for name, value in sdk_groups.items() if value[0]}
    assert set(report_groups) == set(sdk_groups_with_results)
    assert report_groups == {
        name: (completed, rate or 0) for name, (completed, rate) in sdk_groups_with_results.items()
    }
    if history.plan is not None:
        progress_groups = {
            group.display_group: (group.completed, group.success_percentage)
            for group in progress.summary.display_groups
        }
        assert set(progress_groups) == set(sdk_groups)
        assert progress_groups == sdk_groups


async def test_historical_attempt_counts_stay_separate_from_units(sqlite_instance) -> None:
    scenario_result_id = await _persist(sqlite_instance, _HISTORIES["retry_and_resume_recovered"])
    [scenario_result] = await sqlite_instance.get_scenario_results_async(scenario_result_ids=[scenario_result_id])

    statistics = compute_scenario_statistics(scenario_result)

    assert statistics.attempts == 4
    assert statistics.overall.completed == 2
    assert statistics.overall.planned == 2
    assert statistics.overall.errors == 2
    assert statistics.overall.retries == 2
    assert statistics.unattributed_attempts == 0


@pytest.mark.xfail(
    strict=True,
    reason="The history list rejects plans with two seed groups sharing an objective and falls back to legacy "
    "totals, while run detail keeps using the plan. Known gap that predates the shared statistics.",
)
async def test_ambiguous_objective_within_group_agrees_between_list_and_detail(sqlite_instance) -> None:
    history = _History(
        plan=_plan(
            _group(name="attack", eval_hash="eval", seed_ids=["a", "b"]),
            seeds=[_seed("a", "A"), _seed("b", "A")],
        ),
        attempts=[_Attempt("attack", "A", AttackOutcome.SUCCESS)],
    )
    scenario_result_id = await _persist(sqlite_instance, history)

    service = ScenarioRunService()
    detail = await service.get_run_from_storage_async(scenario_result_id=scenario_result_id, active_error=None)
    runs = await service.list_runs_async()
    [list_item] = [item for item in runs.items if item.scenario_result_id == scenario_result_id]

    assert detail is not None
    assert list_item.objective_achieved_rate == detail.objective_achieved_rate
    assert list_item.completed_attacks == detail.completed_attacks


async def test_history_aggregate_flags_runs_with_identifier_only_attempts(sqlite_instance) -> None:
    flagged = await _persist(sqlite_instance, _HISTORIES["identifier_only_then_attributed_only"])
    attributed = await _persist(sqlite_instance, _HISTORIES["one_attributed_seed_group_with_two_identifiers"])

    aggregates = await sqlite_instance.get_scenario_history_aggregates_async(scenario_result_ids=[flagged, attributed])

    assert aggregates[flagged].needs_sdk_statistics
    assert not aggregates[attributed].needs_sdk_statistics
