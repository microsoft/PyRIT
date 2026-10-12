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
from pydantic import TypeAdapter

from pyrit.analytics import compute_scenario_statistics
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.common.utils import to_sha256
from pyrit.memory import AttackResultKeysetCursor, MemoryInterface
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackResultRole,
    AttackSeedGroup,
    ComponentIdentifier,
    OutcomeStatistics,
    ScenarioProducerCounts,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunState,
    SeedObjective,
    SeedPrompt,
)
from pyrit.output._derivation import scenario_overview
from pyrit.output.scenario_result.html import HtmlScenarioReportPrinter
from pyrit.output.scenario_result.json import (
    JsonScenarioResultPrinter,
    build_scenario_full_payload,
)
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
    attack_result_id: str | None = None
    seconds: int | None = None
    result_role: AttackResultRole | str = AttackResultRole.UNKNOWN
    total_retries: int = 0


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
    "empty_technique_hash_matched_by_saved_plan": _History(
        plan=_plan(_group(name="attack", eval_hash="eval", seed_ids=["a"]), seeds=[_seed("a", "A")]),
        attempts=[_Attempt("attack", "A", AttackOutcome.SUCCESS, eval_hash="", seed_group_id="a")],
    ),
    "ambiguous_objectives_with_explicit_attribution": _History(
        plan=_plan(
            _group(name="attack", eval_hash="eval", seed_ids=["a", "b"]),
            seeds=[_seed("a", "A"), _seed("b", "A")],
        ),
        attempts=[
            _Attempt("attack", "A", AttackOutcome.FAILURE, seed_group_id="a"),
            _Attempt("attack", "A", AttackOutcome.SUCCESS, seed_group_id="b"),
        ],
    ),
    "tied_timestamps_use_canonical_attempt_id": _History(
        attempts=[
            _Attempt(
                "attack",
                "A",
                AttackOutcome.SUCCESS,
                seed_group_id="a",
                attack_result_id="ffffffff-ffff-4fff-bfff-000000000001",
                seconds=0,
            ),
            _Attempt(
                "attack",
                "A",
                AttackOutcome.FAILURE,
                seed_group_id="a",
                attack_result_id="00000000-0000-4000-8000-ffffffffffff",
                seconds=0,
            ),
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
    "role_aware_accounting": _History(
        plan=_plan(_group(name="attack", eval_hash="eval", seed_ids=["a"]), seeds=[_seed("a", "A")]),
        attempts=[
            _Attempt(
                "attack", "A", AttackOutcome.SUCCESS, seed_group_id="a", result_role=AttackResultRole.TARGET_FACING
            ),
            _Attempt(
                "attack", "A", AttackOutcome.SUCCESS, seed_group_id="a", result_role=AttackResultRole.TARGET_FACING
            ),
            _Attempt("attack", "A", AttackOutcome.ERROR, seed_group_id="a", result_role=AttackResultRole.TARGET_FACING),
            _Attempt(
                "attack", "A", AttackOutcome.SUCCESS, seed_group_id="a", result_role=AttackResultRole.ORCHESTRATION
            ),
        ],
    ),
}

# Effective-unit success percentages each history must report everywhere (None: no completed unit).
_EXPECTED_OVERALL = {
    "retry_and_resume_recovered": 100,
    "unrecovered_errors": 50,
    "legacy_identities_without_plan": 66,
    "legacy_technique_configurations_sharing_a_name": 50,
    "legacy_error_matched_by_saved_plan": 100,
    "empty_technique_hash_matched_by_saved_plan": 100,
    "ambiguous_objectives_with_explicit_attribution": 50,
    "tied_timestamps_use_canonical_attempt_id": 100,
    "technique_configurations_sharing_a_name": 50,
    "legacy_attempt_with_ambiguous_name": 0,
    "legacy_seed_groups_sharing_an_objective": 50,
    "identifier_and_attributed_rows_of_one_seed_group": 100,
    "explicit_attribution_wins_over_identifier_seeds": 50,
    "one_attributed_seed_group_with_two_identifiers": 100,
    "identifier_only_then_attributed_only": 100,
    "display_groups": 50,
    "empty_history": None,
    "role_aware_accounting": 100,
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
        if attempt.result_role != AttackResultRole.UNKNOWN:
            attribution_data["result_role"] = (
                attempt.result_role.value
                if isinstance(attempt.result_role, AttackResultRole)
                else str(attempt.result_role)
            )
        atomic_attack_identifier = None
        if attempt.seed_context is not None:
            atomic_attack_identifier = AtomicAttackIdentifier.build(
                attack_identifier=ComponentIdentifier(class_name="MockAttack", class_module="tests"),
                seed_group=_seed_group(attempt.objective, attempt.seed_context),
            )
        attack_results.append(
            AttackResult(
                attack_result_id=attempt.attack_result_id or str(uuid.uuid4()),
                conversation_id=f"conversation-{index}",
                objective=attempt.objective,
                outcome=attempt.outcome,
                timestamp=_T0 + timedelta(seconds=index if attempt.seconds is None else attempt.seconds),
                attribution_parent_id=str(scenario_result_id),
                attribution_data=attribution_data,
                atomic_attack_identifier=atomic_attack_identifier,
                total_retries=attempt.total_retries,
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
    assert progress.summary.overall.outcomes == sdk.overall.outcomes
    assert progress.summary.overall.producer_counts == sdk.overall.producer_counts
    assert list_item.producer_counts == sdk.overall.producer_counts

    # Reports
    report = json.loads(await JsonScenarioResultPrinter().render_async(scenario_result))
    assert report["stats"]["overall_success_rate"] == (expected or 0)
    assert report["stats"]["outcomes"] == TypeAdapter(OutcomeStatistics).dump_python(sdk.overall.outcomes, mode="json")
    assert report["stats"]["producer_counts"] == TypeAdapter(ScenarioProducerCounts).dump_python(
        sdk.overall.producer_counts, mode="json"
    )

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
    for group in report["groups"]:
        assert group["outcomes"] == TypeAdapter(OutcomeStatistics).dump_python(
            sdk.display_groups[group["name"]].outcomes, mode="json"
        )
        assert group["producer_counts"] == TypeAdapter(ScenarioProducerCounts).dump_python(
            sdk.display_groups[group["name"]].producer_counts, mode="json"
        )
    if history.plan is not None:
        progress_groups = {
            group.display_group: (group.completed, group.success_percentage)
            for group in progress.summary.display_groups
        }
        assert set(progress_groups) == set(sdk_groups)
        assert progress_groups == sdk_groups
        for group in progress.summary.display_groups:
            assert group.outcomes == sdk.display_groups[group.display_group].outcomes


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
    assert list_item.planned_total_available
    assert list_item.total_attacks == detail.total_attacks == 2
    assert list_item.objective_achieved_rate == detail.objective_achieved_rate
    assert list_item.completed_attacks == detail.completed_attacks == 0


async def test_tied_timestamp_progress_pages_follow_canonical_attempt_id(sqlite_instance: MemoryInterface) -> None:
    history = _HISTORIES["tied_timestamps_use_canonical_attempt_id"]
    run_id = await _persist(sqlite_instance, history)
    expected_ids = sorted(attempt.attack_result_id for attempt in history.attempts if attempt.attack_result_id)
    cursor = None
    for index, expected_id in enumerate(expected_ids):
        page, has_more = await sqlite_instance.get_scenario_attack_result_deltas_async(
            scenario_result_id=run_id, cursor=cursor, limit=1
        )
        assert [delta.attack_result_id for delta in page] == [expected_id]
        assert has_more == (index < len(expected_ids) - 1)
        cursor = AttackResultKeysetCursor(timestamp=page[0].timestamp, attack_result_id=page[0].attack_result_id)
    assert await sqlite_instance.get_scenario_attack_result_deltas_async(
        scenario_result_id=run_id, cursor=cursor, limit=1
    ) == ([], False)


async def test_history_aggregate_flags_runs_with_identifier_only_attempts(sqlite_instance) -> None:
    flagged = await _persist(sqlite_instance, _HISTORIES["identifier_only_then_attributed_only"])
    attributed = await _persist(sqlite_instance, _HISTORIES["one_attributed_seed_group_with_two_identifiers"])

    aggregates = await sqlite_instance.get_scenario_history_aggregates_async(scenario_result_ids=[flagged, attributed])

    assert aggregates[flagged].needs_sdk_statistics
    assert not aggregates[attributed].needs_sdk_statistics


async def test_unmatched_attempts_visible_in_producer_accounting(sqlite_instance) -> None:
    # 4 rows: 1 planned target-facing success, 1 unmatched target-facing error,
    # 1 unmatched orchestration row, and 1 unmatched malformed-role row.
    plan = _plan(
        _group(name="attack", eval_hash="eval", seed_ids=["seed_1"]),
        seeds=[_seed("seed_1", "Objective 1")],
    )
    history = _History(
        plan=plan,
        attempts=[
            _Attempt(
                atomic_attack_name="attack",
                objective="Objective 1",
                outcome=AttackOutcome.SUCCESS,
                seed_group_id="seed_1",
                result_role=AttackResultRole.TARGET_FACING,
            ),
            _Attempt(
                atomic_attack_name="attack",
                objective="Unmatched Objective 2",
                outcome=AttackOutcome.ERROR,
                seed_group_id="unmatched_seed",
                result_role=AttackResultRole.TARGET_FACING,
            ),
            _Attempt(
                atomic_attack_name="orchestrator",
                objective="Unmatched Objective 3",
                outcome=AttackOutcome.SUCCESS,
                result_role=AttackResultRole.ORCHESTRATION,
            ),
            _Attempt(
                atomic_attack_name="attack",
                objective="Unmatched Objective 4",
                outcome=AttackOutcome.ERROR,
                # Malformed role that falls back to UNKNOWN
                result_role="target_facing ",
            ),
        ],
    )
    scenario_result_id = await _persist(sqlite_instance, history)

    # SDK
    [scenario_result] = await sqlite_instance.get_scenario_results_async(scenario_result_ids=[scenario_result_id])
    sdk = compute_scenario_statistics(scenario_result)

    assert sdk.attempts == 4
    assert sdk.unattributed_attempts == 3
    assert sdk.overall.completed == 1
    assert sdk.overall.planned == 1
    assert sdk.overall.succeeded == 1

    # Overall producer accounting includes all 4 rows
    assert sdk.overall.producer_counts.target_facing.attempts == 2
    assert sdk.overall.producer_counts.target_facing.errors == 1
    assert sdk.overall.producer_counts.orchestration.attempts == 1
    assert sdk.overall.producer_counts.orchestration.errors == 0
    assert sdk.overall.producer_counts.unknown.attempts == 1
    assert sdk.overall.producer_counts.unknown.errors == 1

    # API / Service
    service = ScenarioRunService()
    runs = await service.list_runs_async()
    [list_item] = [item for item in runs.items if item.scenario_result_id == scenario_result_id]
    progress = await service.get_run_progress_from_storage_async(
        scenario_result_id=scenario_result_id, since=None, limit=500, active_group_ids=[]
    )

    # Progress & History reconcile with SDK
    assert progress.summary.unattributed_attempts == 3
    assert progress.summary.overall.completed == 1
    assert progress.summary.overall.planned == 1
    assert progress.summary.overall.producer_counts == sdk.overall.producer_counts
    assert list_item.producer_counts == sdk.overall.producer_counts
    assert list_item.completed_attacks == 1

    # Per-group breakdown includes unmatched attempts in recognized group and wholly unplanned group
    attack_atomic = sdk.atomic_attacks["attack"]
    assert attack_atomic.completed == 1
    assert attack_atomic.planned == 1
    assert attack_atomic.producer_counts.target_facing.attempts == 2
    assert attack_atomic.producer_counts.target_facing.errors == 1
    assert attack_atomic.producer_counts.unknown.attempts == 1
    assert attack_atomic.producer_counts.unknown.errors == 1
    assert attack_atomic.producer_counts.orchestration.attempts == 0

    orch_atomic = sdk.atomic_attacks["orchestrator"]
    assert orch_atomic.completed == 0
    assert orch_atomic.planned == 0
    assert orch_atomic.producer_counts.orchestration.attempts == 1
    assert orch_atomic.producer_counts.orchestration.errors == 0

    attack_display = sdk.display_groups["attack"]
    assert attack_display.completed == 1
    assert attack_display.planned == 1
    assert attack_display.producer_counts.target_facing.attempts == 2
    assert attack_display.producer_counts.target_facing.errors == 1
    assert attack_display.producer_counts.unknown.attempts == 1
    assert attack_display.producer_counts.unknown.errors == 1

    orch_display = sdk.display_groups["orchestrator"]
    assert orch_display.completed == 0
    assert orch_display.planned == 0
    assert orch_display.producer_counts.orchestration.attempts == 1
    assert orch_display.producer_counts.orchestration.errors == 0

    # Progress rollups assert group counts directly
    [progress_atomic] = progress.summary.atomic_groups
    assert progress_atomic.atomic_attack_name == "attack"
    assert progress_atomic.completed == 1
    assert progress_atomic.planned == 1
    assert progress_atomic.producer_counts.target_facing.attempts == 2
    assert progress_atomic.producer_counts.target_facing.errors == 1
    assert progress_atomic.producer_counts.unknown.attempts == 1
    assert progress_atomic.producer_counts.unknown.errors == 1

    [progress_display] = progress.summary.display_groups
    assert progress_display.display_group == "attack"
    assert progress_display.completed == 1
    assert progress_display.planned == 1
    assert progress_display.producer_counts.target_facing.attempts == 2
    assert progress_display.producer_counts.target_facing.errors == 1
    assert progress_display.producer_counts.unknown.attempts == 1
    assert progress_display.producer_counts.unknown.errors == 1

    [progress_technique] = progress.summary.techniques
    assert progress_technique.display_group == "attack"
    assert progress_technique.completed == 1
    assert progress_technique.planned == 1
    assert progress_technique.producer_counts.target_facing.attempts == 2
    assert progress_technique.producer_counts.target_facing.errors == 1
    assert progress_technique.producer_counts.unknown.attempts == 1
    assert progress_technique.producer_counts.unknown.errors == 1

    # Overview and JSON report assert group counts directly
    overview = scenario_overview(scenario_result)
    overview_groups = {g.name: g for g in overview.groups}
    assert overview_groups["attack"].objective_executions == 1
    assert overview_groups["attack"].attempts == 3
    assert overview_groups["attack"].producer_counts.target_facing.attempts == 2
    assert overview_groups["attack"].producer_counts.target_facing.errors == 1
    assert overview_groups["attack"].producer_counts.unknown.attempts == 1
    assert overview_groups["attack"].producer_counts.unknown.errors == 1

    assert overview_groups["orchestrator"].objective_executions == 0
    assert overview_groups["orchestrator"].attempts == 1
    assert overview_groups["orchestrator"].producer_counts.orchestration.attempts == 1
    assert overview_groups["orchestrator"].producer_counts.orchestration.errors == 0

    report = json.loads(await JsonScenarioResultPrinter().render_async(scenario_result))
    report_groups = {g["name"]: g for g in report["groups"]}
    assert report_groups["attack"]["num_objective_executions"] == 1
    assert report_groups["attack"]["num_attempts"] == 3
    assert report_groups["attack"]["producer_counts"]["target_facing"]["attempts"] == 2
    assert report_groups["attack"]["producer_counts"]["target_facing"]["errors"] == 1
    assert report_groups["attack"]["producer_counts"]["unknown"]["attempts"] == 1
    assert report_groups["attack"]["producer_counts"]["unknown"]["errors"] == 1

    assert report_groups["orchestrator"]["num_objective_executions"] == 0
    assert report_groups["orchestrator"]["num_attempts"] == 1
    assert report_groups["orchestrator"]["producer_counts"]["orchestration"]["attempts"] == 1
    assert report_groups["orchestrator"]["producer_counts"]["orchestration"]["errors"] == 0


async def test_unmatched_producer_attempts_in_per_group_breakdown(sqlite_instance) -> None:
    # Regression: 1 planned group named "attack", 2 target-facing results with that same group/configuration:
    # a planned success and an error whose seed ID is outside the saved plan.
    plan = _plan(
        _group(name="attack", eval_hash="eval", seed_ids=["seed_1"]),
        seeds=[_seed("seed_1", "Objective 1")],
    )
    history = _History(
        plan=plan,
        attempts=[
            _Attempt(
                atomic_attack_name="attack",
                objective="Objective 1",
                outcome=AttackOutcome.SUCCESS,
                seed_group_id="seed_1",
                result_role=AttackResultRole.TARGET_FACING,
            ),
            _Attempt(
                atomic_attack_name="attack",
                objective="Unmatched Objective 2",
                outcome=AttackOutcome.ERROR,
                seed_group_id="unmatched_seed",
                result_role=AttackResultRole.TARGET_FACING,
            ),
        ],
    )
    scenario_result_id = await _persist(sqlite_instance, history)

    [scenario_result] = await sqlite_instance.get_scenario_results_async(scenario_result_ids=[scenario_result_id])
    sdk = compute_scenario_statistics(scenario_result)

    # Overall: 2 attempts, 1 error, 1 completed/planned logical unit
    assert sdk.overall.completed == 1
    assert sdk.overall.planned == 1
    assert sdk.overall.succeeded == 1
    assert sdk.overall.producer_counts.target_facing.attempts == 2
    assert sdk.overall.producer_counts.target_facing.errors == 1

    # SDK atomic attacks and display groups assert group counts directly
    assert "attack" in sdk.atomic_attacks
    attack_atomic = sdk.atomic_attacks["attack"]
    assert attack_atomic.completed == 1
    assert attack_atomic.planned == 1
    assert attack_atomic.succeeded == 1
    assert attack_atomic.producer_counts.target_facing.attempts == 2
    assert attack_atomic.producer_counts.target_facing.errors == 1

    assert "attack" in sdk.display_groups
    attack_display = sdk.display_groups["attack"]
    assert attack_display.completed == 1
    assert attack_display.planned == 1
    assert attack_display.succeeded == 1
    assert attack_display.producer_counts.target_facing.attempts == 2
    assert attack_display.producer_counts.target_facing.errors == 1

    # ScenarioProgressReadModel: atomic, display, and technique rollups assert group counts directly
    service = ScenarioRunService()
    progress = await service.get_run_progress_from_storage_async(
        scenario_result_id=scenario_result_id, since=None, limit=500, active_group_ids=[]
    )
    assert progress is not None

    [atomic_group] = progress.summary.atomic_groups
    assert atomic_group.atomic_attack_name == "attack"
    assert atomic_group.completed == 1
    assert atomic_group.planned == 1
    assert atomic_group.producer_counts.target_facing.attempts == 2
    assert atomic_group.producer_counts.target_facing.errors == 1

    [display_group] = progress.summary.display_groups
    assert display_group.display_group == "attack"
    assert display_group.completed == 1
    assert display_group.planned == 1
    assert display_group.producer_counts.target_facing.attempts == 2
    assert display_group.producer_counts.target_facing.errors == 1

    [technique] = progress.summary.techniques
    assert technique.display_group == "attack"
    assert technique.completed == 1
    assert technique.planned == 1
    assert technique.producer_counts.target_facing.attempts == 2
    assert technique.producer_counts.target_facing.errors == 1

    # Report overview and JSON printer assert group counts directly
    overview = scenario_overview(scenario_result)
    [group_overview] = overview.groups
    assert group_overview.name == "attack"
    assert group_overview.objective_executions == 1
    assert group_overview.attempts == 2
    assert group_overview.producer_counts.target_facing.attempts == 2
    assert group_overview.producer_counts.target_facing.errors == 1

    report = json.loads(await JsonScenarioResultPrinter().render_async(scenario_result))
    [group_report] = report["groups"]
    assert group_report["name"] == "attack"
    assert group_report["num_objective_executions"] == 1
    assert group_report["num_attempts"] == 2
    assert group_report["producer_counts"]["target_facing"]["attempts"] == 2
    assert group_report["producer_counts"]["target_facing"]["errors"] == 1

    # HTML report displays 2 attempts and 2 target-facing attempts without 2-versus-1 mismatch
    json_overview = JsonScenarioResultPrinter().build(scenario_result, view="overview")
    payload = build_scenario_full_payload(result=scenario_result, overview=json_overview, entries=[])
    html = await HtmlScenarioReportPrinter().render_async(payload)
    assert "<td>attack</td><td>1</td>\n      <td>2</td>\n      <td>2</td>" in html


@pytest.mark.parametrize("eval_hash", ["unplanned-config", None])
@pytest.mark.parametrize("multiple_groups", [False, True])
async def test_same_name_producers_survive_unmatched_configuration(
    sqlite_instance: MemoryInterface, eval_hash: str | None, multiple_groups: bool
) -> None:
    groups = [_group(name="attack", eval_hash="eval", seed_ids=["a"], display_group="planned-one")]
    if multiple_groups:
        groups.append(_group(name="attack", eval_hash="second", seed_ids=["a"], display_group="planned-two"))
    seed_id = "a" if eval_hash else "outside-plan"
    run_id = await _persist(
        sqlite_instance,
        _History(
            plan=_plan(*groups, seeds=[_seed("a", "Reply OK")]),
            display_group_map={"attack": "reported"},
            attempts=[
                _Attempt(
                    "attack",
                    "Reply OK",
                    AttackOutcome.SUCCESS,
                    seed_group_id="a",
                    result_role=AttackResultRole.TARGET_FACING,
                ),
                _Attempt(
                    "attack",
                    "Reply OK",
                    AttackOutcome.ERROR,
                    eval_hash=eval_hash,
                    seed_group_id=seed_id,
                    result_role=AttackResultRole.TARGET_FACING,
                    total_retries=2,
                ),
                _Attempt(
                    "attack",
                    "Reply OK",
                    AttackOutcome.FAILURE,
                    eval_hash=eval_hash,
                    seed_group_id=seed_id,
                    result_role=AttackResultRole.ORCHESTRATION,
                    total_retries=-3,
                ),
                _Attempt(
                    "attack",
                    "Reply OK",
                    AttackOutcome.UNDETERMINED,
                    eval_hash=eval_hash,
                    seed_group_id=seed_id,
                    total_retries=1,
                ),
            ],
        ),
    )
    [scenario] = await sqlite_instance.get_scenario_results_async(scenario_result_ids=[run_id])
    statistics = compute_scenario_statistics(scenario)
    expected = {
        "target_facing": {"attempts": 2, "errors": 1, "retries": 2},
        "orchestration": {"attempts": 1, "errors": 0, "retries": 0},
        "unknown": {"attempts": 1, "errors": 0, "retries": 1},
    }
    assert statistics.overall.producer_counts.model_dump() == expected
    assert statistics.atomic_attacks["attack"].producer_counts.model_dump() == expected
    assert statistics.overall.completed == statistics.overall.succeeded == 1
    assert statistics.overall.planned == len(groups)
    assert statistics.unattributed_attempts == 3
    group_known = eval_hash is None and not multiple_groups
    if group_known:
        assert statistics.display_groups["planned-one"].producer_counts.model_dump() == expected
    else:
        assert statistics.display_groups["reported"].producer_counts.model_dump() == {
            "target_facing": {"attempts": 1, "errors": 1, "retries": 2},
            "orchestration": {"attempts": 1, "errors": 0, "retries": 0},
            "unknown": {"attempts": 1, "errors": 0, "retries": 1},
        }

    report = JsonScenarioResultPrinter().build(scenario)
    [report_group] = report["groups"]
    assert report_group["name"] == "reported"
    assert report_group["num_attempts"] == 4
    assert report_group["num_objective_executions"] == 1
    assert report_group["producer_counts"] == expected
    html = await HtmlScenarioReportPrinter().render_async(
        build_scenario_full_payload(result=scenario, overview=report, entries=[])
    )
    assert "<td>reported</td><td>1</td>\n      <td>4</td>\n      <td>2</td>" in html

    service = ScenarioRunService()
    try:
        detail = await service.get_run_from_storage_async(scenario_result_id=run_id, active_error=None)
        listing = await service.list_runs_async()
        [history] = [item for item in listing.items if item.scenario_result_id == run_id]
        progress = await service.get_run_progress_from_storage_async(
            scenario_result_id=run_id, since=None, limit=500, active_group_ids=[]
        )
        assert detail is not None and progress is not None
        assert detail.producer_counts is not None and history.producer_counts is not None
        assert detail.producer_counts.model_dump() == history.producer_counts.model_dump() == expected
        assert progress.summary.overall.producer_counts.model_dump() == expected
        assert progress.summary.overall.completed == 1
        assert progress.summary.overall.planned == len(groups)
        assert progress.summary.unattributed_attempts == 3
        assert sum(group.producer_counts.target_facing.attempts for group in progress.summary.atomic_groups) == (
            2 if group_known else 1
        )
        assert sum(group.completed for group in progress.summary.atomic_groups) == 1
    finally:
        await service.close_async()


async def test_malformed_roles_fall_back_to_unknown_in_producer_accounting(sqlite_instance) -> None:
    # Verify that malformed role values (trailing spaces or uppercase) fall back to UNKNOWN
    # in both SDK statistics and backend SQL aggregation.
    plan = _plan(
        _group(name="attack", eval_hash="eval", seed_ids=["seed_1"]),
        seeds=[_seed("seed_1", "Objective 1")],
    )
    history = _History(
        plan=plan,
        attempts=[
            _Attempt(
                atomic_attack_name="attack",
                objective="Objective 1",
                outcome=AttackOutcome.SUCCESS,
                seed_group_id="seed_1",
                result_role="target_facing ",  # trailing space
            ),
            _Attempt(
                atomic_attack_name="attack",
                objective="Objective 1",
                outcome=AttackOutcome.SUCCESS,
                seed_group_id="seed_1",
                result_role="TARGET_FACING",  # uppercase
            ),
            _Attempt(
                atomic_attack_name="attack",
                objective="Objective 1",
                outcome=AttackOutcome.SUCCESS,
                seed_group_id="seed_1",
                result_role=AttackResultRole.TARGET_FACING,  # exact canonical match
            ),
        ],
    )
    scenario_result_id = await _persist(sqlite_instance, history)

    [scenario_result] = await sqlite_instance.get_scenario_results_async(scenario_result_ids=[scenario_result_id])
    sdk = compute_scenario_statistics(scenario_result)

    assert sdk.overall.producer_counts.target_facing.attempts == 1
    assert sdk.overall.producer_counts.unknown.attempts == 2

    # Verify backend SQL aggregation also treats both as unknown
    aggregates = await sqlite_instance.get_scenario_history_aggregates_async(scenario_result_ids=[scenario_result_id])
    agg = aggregates[scenario_result_id]
    assert agg.target_facing_attempts == 1
    assert agg.unknown_role_attempts == 2


async def test_ambiguous_objective_with_identifier_recount_updates_producer_fields(sqlite_instance) -> None:
    # Two planned seed groups share an objective.
    # One target-facing attempt carries only its atomic identifier's seeds.
    # SQL flags needs_sdk_statistics=True because objective matching in SQL is ambiguous.
    # The fallback recount restores the unit and MUST copy producer fields.
    planned_seed_a = _seed_group("Shared Objective", "context_a")
    planned_seed_b = _seed_group("Shared Objective", "context_b")
    history = _History(
        plan=_plan(
            _group(name="attack", eval_hash="eval", seed_ids=[planned_seed_a.logical_id, planned_seed_b.logical_id]),
            seeds=[
                ScenarioRunPlanSeedGroup(
                    id=planned_seed_a.logical_id,
                    objective="Shared Objective",
                    objective_sha256=to_sha256("Shared Objective"),
                ),
                ScenarioRunPlanSeedGroup(
                    id=planned_seed_b.logical_id,
                    objective="Shared Objective",
                    objective_sha256=to_sha256("Shared Objective"),
                ),
            ],
        ),
        attempts=[
            _Attempt(
                atomic_attack_name="attack",
                objective="Shared Objective",
                outcome=AttackOutcome.SUCCESS,
                seed_context="context_a",
                result_role=AttackResultRole.TARGET_FACING,
            )
        ],
    )
    scenario_result_id = await _persist(sqlite_instance, history)

    # Check SQL aggregate directly: needs_sdk_statistics is True
    aggregates = await sqlite_instance.get_scenario_history_aggregates_async(scenario_result_ids=[scenario_result_id])
    assert aggregates[scenario_result_id].needs_sdk_statistics

    # SDK
    [scenario_result] = await sqlite_instance.get_scenario_results_async(scenario_result_ids=[scenario_result_id])
    sdk = compute_scenario_statistics(scenario_result)
    assert sdk.overall.completed == 1
    assert sdk.overall.producer_counts.target_facing.attempts == 1

    # API: list_runs_async recounts flagged runs
    service = ScenarioRunService()
    runs = await service.list_runs_async()
    [list_item] = [item for item in runs.items if item.scenario_result_id == scenario_result_id]
    progress = await service.get_run_progress_from_storage_async(
        scenario_result_id=scenario_result_id, since=None, limit=500, active_group_ids=[]
    )

    assert list_item.completed_attacks == 1
    assert list_item.producer_counts.target_facing.attempts == 1
    assert list_item.producer_counts == sdk.overall.producer_counts
    assert progress.summary.overall.producer_counts == sdk.overall.producer_counts
