# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario progress read model."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.services.scenario_progress_read_model import (
    ScenarioProgressReadModel,
    ScenarioProgressSnapshot,
)
from pyrit.memory import AttackResultKeysetCursor
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    ScenarioAttackResultDelta,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
)


def _make_delta(*, run_id: str, index: int = 0) -> ScenarioAttackResultDelta:
    """Create one lightweight persisted row for a synthetic run."""
    return ScenarioAttackResultDelta(
        attack_result_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}-{index}")),
        conversation_id=f"conversation-{run_id}-{index}",
        objective=f"objective-{run_id}-{index}",
        objective_sha256=f"sha-{run_id}-{index}",
        outcome=AttackOutcome.SUCCESS,
        execution_time_ms=10,
        timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
        attribution_data={"parent_collection": "attack", "seed_group_id": f"seed-{index}"},
    )


def _get_snapshot(*, read_model: ScenarioProgressReadModel, run_id: str) -> ScenarioProgressSnapshot:
    """Read one incomplete synthetic run through the public typed boundary."""
    return read_model.get_snapshot(
        scenario_result_id=run_id,
        plan=None,
        plan_complete=False,
        active_group_ids=(),
        terminal=False,
        objective_scorer_identifier=None,
    )


def test_get_snapshot_evicts_least_recently_used_run() -> None:
    memory = MagicMock(spec=MemoryInterface)
    deltas = {run_id: _make_delta(run_id=run_id) for run_id in ("run-a", "run-b", "run-c")}

    def get_deltas(
        *,
        scenario_result_id: str,
        cursor: AttackResultKeysetCursor | None,
        limit: int,
    ) -> tuple[list[ScenarioAttackResultDelta], bool]:
        assert limit == ScenarioProgressReadModel._STORAGE_PAGE_SIZE
        return ([deltas[scenario_result_id]], False) if cursor is None else ([], False)

    memory.get_scenario_attack_result_deltas.side_effect = get_deltas
    read_model = ScenarioProgressReadModel(memory=memory)

    with patch.object(ScenarioProgressReadModel, "_CACHE_MAX_RUNS", 2):
        first = _get_snapshot(read_model=read_model, run_id="run-a")
        _get_snapshot(read_model=read_model, run_id="run-b")
        second = _get_snapshot(read_model=read_model, run_id="run-a")
        _get_snapshot(read_model=read_model, run_id="run-c")
        _get_snapshot(read_model=read_model, run_id="run-b")

    assert isinstance(first, ScenarioProgressSnapshot)
    assert isinstance(first.deltas, tuple)
    assert first.results == second.results
    run_a_cursors = [
        call.kwargs["cursor"]
        for call in memory.get_scenario_attack_result_deltas.call_args_list
        if call.kwargs["scenario_result_id"] == "run-a"
    ]
    run_b_cursors = [
        call.kwargs["cursor"]
        for call in memory.get_scenario_attack_result_deltas.call_args_list
        if call.kwargs["scenario_result_id"] == "run-b"
    ]
    assert run_a_cursors[0] is None
    assert run_a_cursors[1] is not None
    assert run_b_cursors == [None, None]


def test_get_snapshot_invalidates_cache_when_plan_changes() -> None:
    memory = MagicMock(spec=MemoryInterface)
    delta = _make_delta(run_id="run-plan")
    memory.get_scenario_attack_result_deltas.return_value = ([delta], False)
    read_model = ScenarioProgressReadModel(memory=memory)

    def make_plan(group_id: str) -> ScenarioRunPlan:
        return ScenarioRunPlan(
            atomic_groups=[
                ScenarioRunPlanAtomicGroup(
                    id=group_id,
                    atomic_attack_name="attack",
                    display_group="Attack",
                    technique_eval_hash="",
                    seed_group_ids=["seed-0"],
                )
            ],
            seed_groups=[
                ScenarioRunPlanSeedGroup(
                    id="seed-0",
                    objective_sha256=delta.objective_sha256 or "",
                    objective=delta.objective,
                )
            ],
        )

    first = read_model.get_snapshot(
        scenario_result_id="run-plan",
        plan=make_plan("group-a"),
        plan_complete=True,
        active_group_ids=(),
        terminal=False,
        objective_scorer_identifier=None,
    )
    second = read_model.get_snapshot(
        scenario_result_id="run-plan",
        plan=make_plan("group-b"),
        plan_complete=True,
        active_group_ids=(),
        terminal=False,
        objective_scorer_identifier=None,
    )

    assert first.results[0].atomic_group_id == "group-a"
    assert second.results[0].atomic_group_id == "group-b"
    assert [call.kwargs["cursor"] for call in memory.get_scenario_attack_result_deltas.call_args_list] == [
        None,
        None,
    ]


@pytest.mark.parametrize(
    ("active_group_ids", "terminal", "plan_complete", "expected_status", "expected_planned"),
    [
        pytest.param(("group",), False, True, "RUNNING", 2, id="active-group-change"),
        pytest.param((), True, True, "INCOMPLETE", 2, id="terminal-state-change"),
        pytest.param((), False, False, "PENDING", None, id="plan-completeness-change"),
    ],
)
def test_get_snapshot_updates_summary_without_new_rows(
    *,
    active_group_ids: tuple[str, ...],
    terminal: bool,
    plan_complete: bool,
    expected_status: str,
    expected_planned: int | None,
) -> None:
    memory = MagicMock(spec=MemoryInterface)
    deltas = [_make_delta(run_id="run-state", index=index) for index in range(2)]
    plan = ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id="group",
                atomic_attack_name="attack",
                display_group="Attack",
                technique_eval_hash="",
                seed_group_ids=["seed-0", "seed-1"],
            )
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(
                id=f"seed-{index}",
                objective_sha256=delta.objective_sha256 or "",
                objective=delta.objective,
            )
            for index, delta in enumerate(deltas)
        ],
    )
    memory.get_scenario_attack_result_deltas.side_effect = [([deltas[0]], False), ([], False), ([], False)]
    read_model = ScenarioProgressReadModel(memory=memory)

    with patch.object(read_model, "_map_progress_delta", wraps=read_model._map_progress_delta) as map_delta:
        first = read_model.get_snapshot(
            scenario_result_id="run-state",
            plan=plan,
            plan_complete=True,
            active_group_ids=(),
            terminal=False,
            objective_scorer_identifier=None,
        )
        second = read_model.get_snapshot(
            scenario_result_id="run-state",
            plan=plan,
            plan_complete=plan_complete,
            active_group_ids=active_group_ids,
            terminal=terminal,
            objective_scorer_identifier=None,
        )
        restored = read_model.get_snapshot(
            scenario_result_id="run-state",
            plan=plan,
            plan_complete=True,
            active_group_ids=(),
            terminal=False,
            objective_scorer_identifier=None,
        )

    assert first.summary.atomic_groups[0].status == "PENDING"
    assert first.summary.overall.planned == 2
    assert second.summary.atomic_groups[0].status == expected_status
    assert second.summary.overall.planned == expected_planned
    assert second.summary.overall.completed == 1
    assert second.summary.overall.succeeded == 1
    assert restored.summary == first.summary
    assert first.results == second.results == restored.results
    map_delta.assert_called_once()
    cursor = AttackResultKeysetCursor(
        timestamp=deltas[0].timestamp,
        attack_result_id=deltas[0].attack_result_id,
    )
    assert [call.kwargs["cursor"] for call in memory.get_scenario_attack_result_deltas.call_args_list] == [
        None,
        cursor,
        cursor,
    ]


def test_get_snapshot_keeps_concurrent_runs_isolated() -> None:
    memory = MagicMock(spec=MemoryInterface)

    def get_deltas(
        *,
        scenario_result_id: str,
        cursor: AttackResultKeysetCursor | None,
        limit: int,
    ) -> tuple[list[ScenarioAttackResultDelta], bool]:
        assert limit == ScenarioProgressReadModel._STORAGE_PAGE_SIZE
        return ([_make_delta(run_id=scenario_result_id)], False) if cursor is None else ([], False)

    memory.get_scenario_attack_result_deltas.side_effect = get_deltas
    read_model = ScenarioProgressReadModel(memory=memory)
    run_ids = [f"run-{index}" for index in range(8)]

    def read_run(run_id: str) -> ScenarioProgressSnapshot:
        return _get_snapshot(read_model=read_model, run_id=run_id)

    with ThreadPoolExecutor(max_workers=4) as executor:
        snapshots = list(executor.map(read_run, run_ids))

    assert [snapshot.results[0].conversation_id for snapshot in snapshots] == [
        f"conversation-{run_id}-0" for run_id in run_ids
    ]
