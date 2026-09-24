# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario progress read model."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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


async def _get_snapshot_async(*, read_model: ScenarioProgressReadModel, run_id: str) -> ScenarioProgressSnapshot:
    """Read one incomplete synthetic run through the public typed boundary."""
    return await read_model.get_snapshot_async(
        scenario_result_id=run_id,
        plan=None,
        plan_complete=False,
        active_group_ids=(),
        terminal=False,
        objective_scorer_identifier=None,
    )


async def test_get_snapshot_evicts_least_recently_used_run() -> None:
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

    memory.get_scenario_attack_result_deltas_async = AsyncMock(side_effect=get_deltas)
    read_model = ScenarioProgressReadModel(memory=memory)

    with patch.object(ScenarioProgressReadModel, "_CACHE_MAX_RUNS", 2):
        first = await _get_snapshot_async(read_model=read_model, run_id="run-a")
        (await _get_snapshot_async(read_model=read_model, run_id="run-b"))
        second = await _get_snapshot_async(read_model=read_model, run_id="run-a")
        (await _get_snapshot_async(read_model=read_model, run_id="run-c"))
        (await _get_snapshot_async(read_model=read_model, run_id="run-b"))

    assert isinstance(first, ScenarioProgressSnapshot)
    assert isinstance(first.deltas, tuple)
    assert first.results == second.results
    run_a_cursors = [
        call.kwargs["cursor"]
        for call in memory.get_scenario_attack_result_deltas_async.call_args_list
        if call.kwargs["scenario_result_id"] == "run-a"
    ]
    run_b_cursors = [
        call.kwargs["cursor"]
        for call in memory.get_scenario_attack_result_deltas_async.call_args_list
        if call.kwargs["scenario_result_id"] == "run-b"
    ]
    assert run_a_cursors[0] is None
    assert run_a_cursors[1] is not None
    assert run_b_cursors == [None, None]


async def test_cancelled_refresh_releases_lock_and_updates_partially_loaded_summary() -> None:
    memory = MagicMock(spec=MemoryInterface)
    first = _make_delta(run_id="run", index=0)
    second = _make_delta(run_id="run", index=1)
    memory.get_scenario_attack_result_deltas_async.return_value = ([first], False)
    read_model = ScenarioProgressReadModel(memory=memory)
    initial = await _get_snapshot_async(read_model=read_model, run_id="run")
    assert initial.summary.overall.completed == 1
    blocked = asyncio.Event()

    async def get_deltas_async(
        *, scenario_result_id: str, cursor: AttackResultKeysetCursor | None, limit: int
    ) -> tuple[list[ScenarioAttackResultDelta], bool]:
        if cursor is not None and cursor.attack_result_id == first.attack_result_id:
            return [second], True
        blocked.set()
        await asyncio.Event().wait()
        return [], False

    memory.get_scenario_attack_result_deltas_async.side_effect = get_deltas_async
    task = asyncio.create_task(_get_snapshot_async(read_model=read_model, run_id="run"))
    await asyncio.wait_for(blocked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    memory.get_scenario_attack_result_deltas_async = AsyncMock(return_value=([], False))
    refreshed = await asyncio.wait_for(_get_snapshot_async(read_model=read_model, run_id="run"), timeout=5)
    assert len(refreshed.results) == 2
    assert refreshed.summary.overall.completed == 2
    assert refreshed.summary.overall.succeeded == 2


async def test_get_snapshot_invalidates_cache_when_plan_changes() -> None:
    memory = MagicMock(spec=MemoryInterface)
    delta = _make_delta(run_id="run-plan")
    memory.get_scenario_attack_result_deltas_async = AsyncMock(return_value=([delta], False))
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

    first = await read_model.get_snapshot_async(
        scenario_result_id="run-plan",
        plan=make_plan("group-a"),
        plan_complete=True,
        active_group_ids=(),
        terminal=False,
        objective_scorer_identifier=None,
    )
    second = await read_model.get_snapshot_async(
        scenario_result_id="run-plan",
        plan=make_plan("group-b"),
        plan_complete=True,
        active_group_ids=(),
        terminal=False,
        objective_scorer_identifier=None,
    )

    assert first.results[0].atomic_group_id == "group-a"
    assert second.results[0].atomic_group_id == "group-b"
    assert [call.kwargs["cursor"] for call in memory.get_scenario_attack_result_deltas_async.call_args_list] == [
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
async def test_get_snapshot_updates_summary_without_new_rows(
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
    memory.get_scenario_attack_result_deltas_async = AsyncMock(
        side_effect=[([deltas[0]], False), ([], False), ([], False)]
    )
    read_model = ScenarioProgressReadModel(memory=memory)

    with patch.object(read_model, "_map_progress_delta", wraps=read_model._map_progress_delta) as map_delta:
        first = await read_model.get_snapshot_async(
            scenario_result_id="run-state",
            plan=plan,
            plan_complete=True,
            active_group_ids=(),
            terminal=False,
            objective_scorer_identifier=None,
        )
        second = await read_model.get_snapshot_async(
            scenario_result_id="run-state",
            plan=plan,
            plan_complete=plan_complete,
            active_group_ids=active_group_ids,
            terminal=terminal,
            objective_scorer_identifier=None,
        )
        restored = await read_model.get_snapshot_async(
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
    assert [call.kwargs["cursor"] for call in memory.get_scenario_attack_result_deltas_async.call_args_list] == [
        None,
        cursor,
        cursor,
    ]


async def test_get_snapshot_keeps_concurrent_runs_isolated() -> None:
    memory = MagicMock(spec=MemoryInterface)

    def get_deltas(
        *,
        scenario_result_id: str,
        cursor: AttackResultKeysetCursor | None,
        limit: int,
    ) -> tuple[list[ScenarioAttackResultDelta], bool]:
        assert limit == ScenarioProgressReadModel._STORAGE_PAGE_SIZE
        return ([_make_delta(run_id=scenario_result_id)], False) if cursor is None else ([], False)

    memory.get_scenario_attack_result_deltas_async = AsyncMock(side_effect=get_deltas)
    read_model = ScenarioProgressReadModel(memory=memory)
    run_ids = [f"run-{index}" for index in range(8)]

    async def read_run_async(run_id: str) -> ScenarioProgressSnapshot:
        return await _get_snapshot_async(read_model=read_model, run_id=run_id)

    snapshots = await asyncio.gather(*(read_run_async(run_id) for run_id in run_ids))

    assert [snapshot.results[0].conversation_id for snapshot in snapshots] == [
        f"conversation-{run_id}-0" for run_id in run_ids
    ]
