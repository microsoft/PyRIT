# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario progress read model."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.analytics.scenario_statistics import compute_scenario_statistics
from pyrit.backend.models.attacks import UpdateAttackRequest
from pyrit.backend.services.attack_service import AttackService
from pyrit.backend.services.scenario_progress_read_model import (
    ScenarioPlanLookup,
    ScenarioProgressReadModel,
    ScenarioProgressSnapshot,
)
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.exceptions import ScenarioPartialFailureException
from pyrit.executor.attack import (
    AttackScoringConfig,
    PromptSendingAttack,
    SequentialAttack,
    SequentialChildAttack,
)
from pyrit.executor.attack.core.attack_preparation import AttackPreparationFailure, AttackPreparationFailureKind
from pyrit.memory import AttackResultKeysetCursor
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    AttackResultRole,
    AttackSeedGroup,
    ComponentIdentifier,
    Message,
    ScenarioAttackResultDelta,
    ScenarioProgressResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanGroupKind,
    ScenarioRunPlanSeedGroup,
    ScenarioRunState,
    SeedObjective,
)
from pyrit.prompt_target import PromptTarget
from pyrit.scenario import DatasetConfiguration
from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy, Scenario, ScenarioTechnique
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.matrix_atomic_attack_builder import build_baseline_atomic_attack
from pyrit.scenario.scenarios.adaptive.dispatcher import (
    ADAPTIVE_ATTEMPT_LABEL,
    AdaptiveTechniqueDispatcher,
    TechniqueBundle,
)
from pyrit.score import Scorer, SubStringScorer
from unit.async_utils import get_defined_tasks
from unit.mocks import MockPromptTarget, get_mock_target_identifier, make_scenario_result


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


@pytest.mark.parametrize("interruption", ["cancellation", "storage-error"])
async def test_interrupted_refresh_releases_lock_and_updates_partially_loaded_summary(interruption: str) -> None:
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
        if interruption == "storage-error":
            raise RuntimeError("storage failed")
        await asyncio.Event().wait()
        return [], False

    memory.get_scenario_attack_result_deltas_async.side_effect = get_deltas_async
    task = asyncio.create_task(_get_snapshot_async(read_model=read_model, run_id="run"))
    try:
        await asyncio.wait_for(blocked.wait(), timeout=5)
        if interruption == "cancellation":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="storage failed"):
                await task
        assert not read_model._refresh_states

        memory.get_scenario_attack_result_deltas_async = AsyncMock(return_value=([], False))
        refreshed = await asyncio.wait_for(_get_snapshot_async(read_model=read_model, run_id="run"), timeout=5)
        assert refreshed.deltas == (first, second)
        assert [result.attack_result_id for result in refreshed.results] == [
            first.attack_result_id,
            second.attack_result_id,
        ]
        assert refreshed.summary.overall.completed == 2
        assert refreshed.summary.overall.succeeded == 2
        assert memory.get_scenario_attack_result_deltas_async.call_args.kwargs["cursor"] == AttackResultKeysetCursor(
            timestamp=second.timestamp, attack_result_id=second.attack_result_id
        )
        assert not read_model._refresh_states
        assert initial.deltas == (first,)
        assert initial.summary.overall.completed == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_async_snapshots_share_cache_and_cursor() -> None:
    memory = MagicMock(spec=MemoryInterface)
    deltas = [_make_delta(run_id="shared", index=index) for index in range(3)]
    memory.get_scenario_attack_result_deltas_async.side_effect = [
        ([deltas[0]], False),
        ([deltas[1]], False),
        ([deltas[2]], False),
    ]
    read_model = ScenarioProgressReadModel(memory=memory)

    first = await _get_snapshot_async(read_model=read_model, run_id="shared")
    second = await _get_snapshot_async(read_model=read_model, run_id="shared")
    third = await _get_snapshot_async(read_model=read_model, run_id="shared")

    assert [snapshot.summary.overall.completed for snapshot in (first, second, third)] == [1, 2, 3]
    assert [call.kwargs["cursor"] for call in memory.get_scenario_attack_result_deltas_async.call_args_list] == [
        None,
        AttackResultKeysetCursor(timestamp=deltas[0].timestamp, attack_result_id=deltas[0].attack_result_id),
        AttackResultKeysetCursor(timestamp=deltas[1].timestamp, attack_result_id=deltas[1].attack_result_id),
    ]
    memory.get_scenario_attack_result_deltas.assert_not_called()
    assert first.deltas == (deltas[0],)
    assert second.deltas == tuple(deltas[:2])
    assert third.deltas == tuple(deltas)


async def test_cancelled_waiter_preserves_run_lock_without_blocking_other_runs() -> None:
    memory = MagicMock(spec=MemoryInterface)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def get_deltas_async(
        *, scenario_result_id: str, cursor: AttackResultKeysetCursor | None, limit: int
    ) -> tuple[list[ScenarioAttackResultDelta], bool]:
        if scenario_result_id == "owner" and cursor is None:
            entered.set()
            await release.wait()
        return ([_make_delta(run_id=scenario_result_id)], False) if cursor is None else ([], False)

    memory.get_scenario_attack_result_deltas_async.side_effect = get_deltas_async
    read_model = ScenarioProgressReadModel(memory=memory)
    owner = asyncio.create_task(_get_snapshot_async(read_model=read_model, run_id="owner"))

    async def wait_for_snapshot_async(started: asyncio.Event) -> ScenarioProgressSnapshot:
        started.set()
        return await _get_snapshot_async(read_model=read_model, run_id="owner")

    waiter = None
    successor = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        state = read_model._refresh_states["owner"]
        waiter_started = asyncio.Event()
        waiter = asyncio.create_task(wait_for_snapshot_async(waiter_started))
        await asyncio.wait_for(waiter_started.wait(), timeout=5)
        assert not waiter.done()
        assert state.callers == 2
        memory.get_scenario_attack_result_deltas_async.assert_awaited_once()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert read_model._refresh_states["owner"] is state
        assert state.lock.locked()
        assert state.callers == 1

        successor_started = asyncio.Event()
        successor = asyncio.create_task(wait_for_snapshot_async(successor_started))
        await asyncio.wait_for(successor_started.wait(), timeout=5)
        assert not successor.done()
        assert state.callers == 2
        memory.get_scenario_attack_result_deltas_async.assert_awaited_once()

        independent = await asyncio.wait_for(
            _get_snapshot_async(read_model=read_model, run_id="independent"), timeout=5
        )
        assert independent.results[0].conversation_id == "conversation-independent-0"
        assert not owner.done()
        assert not successor.done()
        assert read_model._refresh_states["owner"] is state

        release.set()
        snapshots = await asyncio.wait_for(asyncio.gather(owner, successor), timeout=5)
        assert [snapshot.results[0].conversation_id for snapshot in snapshots] == [
            "conversation-owner-0",
            "conversation-owner-0",
        ]
        owner_cursors = [
            call.kwargs["cursor"]
            for call in memory.get_scenario_attack_result_deltas_async.call_args_list
            if call.kwargs["scenario_result_id"] == "owner"
        ]
        assert owner_cursors == [
            None,
            AttackResultKeysetCursor(
                timestamp=snapshots[0].deltas[0].timestamp,
                attack_result_id=snapshots[0].deltas[0].attack_result_id,
            ),
        ]
        assert not state.lock.locked()
        assert not read_model._refresh_states
    finally:
        release.set()
        tasks = get_defined_tasks(owner, waiter, successor)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("owner_exit", ["success", "cancellation", "storage-error"])
@patch.object(ScenarioProgressReadModel, "_CACHE_MAX_RUNS", 1)
async def test_owner_handoff_keeps_waiter_cache_under_pressure(owner_exit: str) -> None:
    memory = MagicMock(spec=MemoryInterface)
    first = _make_delta(run_id="run", index=0)
    second = _make_delta(run_id="run", index=1)
    owner_blocked = asyncio.Event()
    waiter_blocked = asyncio.Event()
    other_blocked = asyncio.Event()
    release_owner = asyncio.Event()
    release_waiter = asyncio.Event()
    release_other = asyncio.Event()
    cursors: list[AttackResultKeysetCursor | None] = []

    async def get_deltas_async(
        *, scenario_result_id: str, cursor: AttackResultKeysetCursor | None, limit: int
    ) -> tuple[list[ScenarioAttackResultDelta], bool]:
        if scenario_result_id == "other":
            other_blocked.set()
            await release_other.wait()
            return [_make_delta(run_id="other")], False
        cursors.append(cursor)
        if len(cursors) == 1:
            return [first], True
        if len(cursors) == 2:
            owner_blocked.set()
            await release_owner.wait()
            if owner_exit == "storage-error":
                raise RuntimeError("storage failed")
            return [], False
        if len(cursors) == 3:
            waiter_blocked.set()
            await release_waiter.wait()
            return [second], False
        return [], False

    memory.get_scenario_attack_result_deltas_async = AsyncMock(side_effect=get_deltas_async)
    read_model = ScenarioProgressReadModel(memory=memory)

    async def wait_for_snapshot_async(started: asyncio.Event) -> ScenarioProgressSnapshot:
        started.set()
        return await _get_snapshot_async(read_model=read_model, run_id="run")

    owner = asyncio.create_task(_get_snapshot_async(read_model=read_model, run_id="run"))
    waiter = None
    successor = None
    other = None
    try:
        await asyncio.wait_for(owner_blocked.wait(), timeout=5)
        state = read_model._refresh_states["run"]
        entry = read_model._cache["run"]
        waiter_started = asyncio.Event()
        waiter = asyncio.create_task(wait_for_snapshot_async(waiter_started))
        await asyncio.wait_for(waiter_started.wait(), timeout=5)
        assert state.callers == 2
        assert not waiter.done()

        other = asyncio.create_task(_get_snapshot_async(read_model=read_model, run_id="other"))
        await asyncio.wait_for(other_blocked.wait(), timeout=5)
        assert list(read_model._cache) == ["run", "other"]
        assert set(read_model._refresh_states) == {"run", "other"}

        if owner_exit == "cancellation":
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
        else:
            release_owner.set()
            if owner_exit == "storage-error":
                with pytest.raises(RuntimeError, match="storage failed"):
                    await asyncio.wait_for(owner, timeout=5)
            else:
                initial = await asyncio.wait_for(owner, timeout=5)
                assert initial.deltas == (first,)
        await asyncio.wait_for(waiter_blocked.wait(), timeout=5)
        assert read_model._refresh_states["run"] is state
        assert state.callers == 1
        assert state.lock.locked()
        assert read_model._cache["run"] is entry
        assert len(read_model._cache) == 2
        assert not other.done()

        successor_started = asyncio.Event()
        successor = asyncio.create_task(wait_for_snapshot_async(successor_started))
        await asyncio.wait_for(successor_started.wait(), timeout=5)
        assert read_model._refresh_states["run"] is state
        assert state.callers == 2
        assert not successor.done()
        assert len(cursors) == 3

        release_waiter.set()
        snapshots = await asyncio.wait_for(asyncio.gather(waiter, successor), timeout=5)
        assert all(snapshot.deltas == (first, second) for snapshot in snapshots)
        assert all(snapshot.summary.overall.completed == 2 for snapshot in snapshots)
        first_cursor = AttackResultKeysetCursor(timestamp=first.timestamp, attack_result_id=first.attack_result_id)
        second_cursor = AttackResultKeysetCursor(timestamp=second.timestamp, attack_result_id=second.attack_result_id)
        assert cursors == [None, first_cursor, first_cursor, second_cursor]
        assert not state.lock.locked()
        assert list(read_model._cache) == ["other"]
        assert set(read_model._refresh_states) == {"other"}

        release_other.set()
        await asyncio.wait_for(other, timeout=5)
        assert len(read_model._cache) == 1
        assert not read_model._refresh_states
    finally:
        release_owner.set()
        release_waiter.set()
        release_other.set()
        tasks = get_defined_tasks(owner, waiter, successor, other)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_storage_error_releases_refresh_lock() -> None:
    memory = MagicMock(spec=MemoryInterface)
    memory.get_scenario_attack_result_deltas_async.side_effect = RuntimeError("storage failed")
    read_model = ScenarioProgressReadModel(memory=memory)

    with pytest.raises(RuntimeError, match="storage failed"):
        await _get_snapshot_async(read_model=read_model, run_id="run")
    assert not read_model._refresh_states

    memory.get_scenario_attack_result_deltas_async.side_effect = None
    memory.get_scenario_attack_result_deltas_async.return_value = ([_make_delta(run_id="run")], False)
    snapshot = await asyncio.wait_for(_get_snapshot_async(read_model=read_model, run_id="run"), timeout=5)
    assert snapshot.summary.overall.completed == 1
    assert not read_model._refresh_states


async def test_snapshot_error_releases_refresh_ownership() -> None:
    memory = MagicMock(spec=MemoryInterface)
    delta = _make_delta(run_id="run")
    memory.get_scenario_attack_result_deltas_async = AsyncMock(side_effect=[([delta], False), ([], False)])
    read_model = ScenarioProgressReadModel(memory=memory)

    with patch.object(read_model, "_build_snapshot", side_effect=RuntimeError("snapshot failed")):
        with pytest.raises(RuntimeError, match="snapshot failed"):
            await _get_snapshot_async(read_model=read_model, run_id="run")
    assert not read_model._refresh_states

    snapshot = await _get_snapshot_async(read_model=read_model, run_id="run")
    assert snapshot.deltas == (delta,)
    assert snapshot.summary.overall.completed == 1
    assert memory.get_scenario_attack_result_deltas_async.call_args.kwargs["cursor"] == AttackResultKeysetCursor(
        timestamp=delta.timestamp, attack_result_id=delta.attack_result_id
    )
    assert not read_model._refresh_states


async def test_get_snapshot_protects_run_with_waiters_from_lru_eviction() -> None:
    memory = MagicMock(spec=MemoryInterface)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def get_deltas_async(
        *, scenario_result_id: str, cursor: AttackResultKeysetCursor | None, limit: int
    ) -> tuple[list[ScenarioAttackResultDelta], bool]:
        if scenario_result_id == "protected" and cursor is None:
            entered.set()
            await release.wait()
        return ([_make_delta(run_id=scenario_result_id)], False) if cursor is None else ([], False)

    memory.get_scenario_attack_result_deltas_async = AsyncMock(side_effect=get_deltas_async)
    read_model = ScenarioProgressReadModel(memory=memory)

    async def wait_for_snapshot_async(started: asyncio.Event) -> ScenarioProgressSnapshot:
        started.set()
        return await _get_snapshot_async(read_model=read_model, run_id="protected")

    owner = None
    waiter = None
    successor = None
    try:
        with patch.object(ScenarioProgressReadModel, "_CACHE_MAX_RUNS", 2):
            await _get_snapshot_async(read_model=read_model, run_id="idle-a")
            await _get_snapshot_async(read_model=read_model, run_id="idle-b")
            owner = asyncio.create_task(_get_snapshot_async(read_model=read_model, run_id="protected"))
            await asyncio.wait_for(entered.wait(), timeout=5)
            state = read_model._refresh_states["protected"]
            entry = read_model._cache["protected"]
            assert list(read_model._cache) == ["idle-b", "protected"]

            waiter_started = asyncio.Event()
            waiter = asyncio.create_task(wait_for_snapshot_async(waiter_started))
            await asyncio.wait_for(waiter_started.wait(), timeout=5)
            await _get_snapshot_async(read_model=read_model, run_id="idle-b")
            await _get_snapshot_async(read_model=read_model, run_id="idle-c")
            assert list(read_model._cache) == ["protected", "idle-c"]
            assert read_model._cache["protected"] is entry

            successor_started = asyncio.Event()
            successor = asyncio.create_task(wait_for_snapshot_async(successor_started))
            await asyncio.wait_for(successor_started.wait(), timeout=5)
            assert read_model._refresh_states["protected"] is state
            assert state.callers == 3
            assert not waiter.done()
            assert not successor.done()

            release.set()
            snapshots = await asyncio.wait_for(asyncio.gather(owner, waiter, successor), timeout=5)
            delta = snapshots[0].deltas[0]
            cursor = AttackResultKeysetCursor(timestamp=delta.timestamp, attack_result_id=delta.attack_result_id)
            assert all(snapshot.deltas == (delta,) for snapshot in snapshots)
            assert [
                call.kwargs["cursor"]
                for call in memory.get_scenario_attack_result_deltas_async.call_args_list
                if call.kwargs["scenario_result_id"] == "protected"
            ] == [None, cursor, cursor]
            assert read_model._cache["protected"] is entry
            assert len(read_model._cache) == 2
            assert not read_model._refresh_states
    finally:
        release.set()
        tasks = get_defined_tasks(owner, waiter, successor)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("first_exit", ["success", "cancellation", "storage-error"])
async def test_protected_cache_overflow_is_trimmed_when_callers_exit(first_exit: str) -> None:
    memory = MagicMock(spec=MemoryInterface)
    run_ids = ("run-a", "run-b", "run-c")
    entered = {run_id: asyncio.Event() for run_id in run_ids}
    release = {run_id: asyncio.Event() for run_id in run_ids}

    async def get_deltas_async(
        *, scenario_result_id: str, cursor: AttackResultKeysetCursor | None, limit: int
    ) -> tuple[list[ScenarioAttackResultDelta], bool]:
        entered[scenario_result_id].set()
        await release[scenario_result_id].wait()
        if scenario_result_id == "run-a" and first_exit == "storage-error":
            raise RuntimeError("storage failed")
        return [_make_delta(run_id=scenario_result_id)], False

    memory.get_scenario_attack_result_deltas_async = AsyncMock(side_effect=get_deltas_async)
    read_model = ScenarioProgressReadModel(memory=memory)
    tasks = {
        run_id: asyncio.create_task(_get_snapshot_async(read_model=read_model, run_id=run_id)) for run_id in run_ids
    }
    try:
        with patch.object(ScenarioProgressReadModel, "_CACHE_MAX_RUNS", 1):
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), timeout=5)
            assert len(read_model._cache) == 3
            assert set(read_model._refresh_states) == set(run_ids)

            if first_exit == "cancellation":
                tasks["run-a"].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await tasks["run-a"]
            else:
                release["run-a"].set()
                if first_exit == "storage-error":
                    with pytest.raises(RuntimeError, match="storage failed"):
                        await tasks["run-a"]
                else:
                    await asyncio.wait_for(tasks["run-a"], timeout=5)
            assert list(read_model._cache) == ["run-b", "run-c"]
            assert set(read_model._refresh_states) == {"run-b", "run-c"}

            release["run-b"].set()
            await asyncio.wait_for(tasks["run-b"], timeout=5)
            assert list(read_model._cache) == ["run-c"]
            assert set(read_model._refresh_states) == {"run-c"}

            release["run-c"].set()
            await asyncio.wait_for(tasks["run-c"], timeout=5)
            assert list(read_model._cache) == ["run-c"]
            assert not read_model._refresh_states
    finally:
        for event in release.values():
            event.set()
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)


async def test_get_snapshot_invalidates_cache_when_plan_changes_while_waiting() -> None:
    memory = MagicMock(spec=MemoryInterface)
    delta = _make_delta(run_id="run-plan")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def get_deltas_async(
        *, scenario_result_id: str, cursor: AttackResultKeysetCursor | None, limit: int
    ) -> tuple[list[ScenarioAttackResultDelta], bool]:
        if not entered.is_set():
            entered.set()
            await release.wait()
        return [delta], False

    memory.get_scenario_attack_result_deltas_async = AsyncMock(side_effect=get_deltas_async)
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

    waiter_started = asyncio.Event()

    async def read_plan_async(group_id: str) -> ScenarioProgressSnapshot:
        if group_id == "group-b":
            waiter_started.set()
        return await read_model.get_snapshot_async(
            scenario_result_id="run-plan",
            plan=make_plan(group_id),
            plan_complete=True,
            active_group_ids=(),
            terminal=False,
            objective_scorer_identifier=None,
        )

    owner = asyncio.create_task(read_plan_async("group-a"))
    waiter = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        state = read_model._refresh_states["run-plan"]
        waiter = asyncio.create_task(read_plan_async("group-b"))
        await asyncio.wait_for(waiter_started.wait(), timeout=5)
        assert state.callers == 2
        assert not waiter.done()
        memory.get_scenario_attack_result_deltas_async.assert_awaited_once()

        release.set()
        first, second = await asyncio.wait_for(asyncio.gather(owner, waiter), timeout=5)
        assert first.results[0].atomic_group_id == "group-a"
        assert second.results[0].atomic_group_id == "group-b"
        assert [call.kwargs["cursor"] for call in memory.get_scenario_attack_result_deltas_async.call_args_list] == [
            None,
            None,
        ]
        assert not state.lock.locked()
        assert not read_model._refresh_states
    finally:
        release.set()
        tasks = get_defined_tasks(owner, waiter)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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


class _UnavailableTarget(MockPromptTarget):
    """A target whose every request fails, so the attacks that call it end in errors."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        raise RuntimeError("objective target unavailable")


class _RoleScenario(Scenario):
    """Minimal scenario that runs the atomic attacks a test builds, through the real plan and run path."""

    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    def __init__(self, *, build_atomic_attacks: Callable[[PromptTarget], list[AtomicAttack]]) -> None:
        class _Technique(ScenarioTechnique):
            TEST = ("test", {"concrete"})
            ALL = ("all", {"all"})

            @classmethod
            def get_aggregate_tags(cls) -> set[str]:
                return {"all"}

        scorer = MagicMock(spec=Scorer)
        scorer.get_identifier.return_value = ComponentIdentifier(class_name="RoleTestScorer", class_module="tests")
        scorer.get_scorer_metrics.return_value = None
        super().__init__(
            name="RoleScenario",
            version=1,
            technique_class=_Technique,
            default_dataset_config=DatasetConfiguration(),
            objective_scorer=scorer,
        )
        self._build = build_atomic_attacks

    async def _resolve_seed_groups_by_dataset_async(self, *, apply_sampling: bool = True):
        return {}

    async def _build_atomic_attacks_async(self, *, context):
        return self._build(context.objective_target)


_SEED_GROUP = AttackSeedGroup(seeds=[SeedObjective(value="describe the objective")])


def _prompt_sending(*, target: PromptTarget) -> PromptSendingAttack:
    # The mock target always answers "default", so this scorer always reports FAILURE and
    # FIRST_SUCCESS keeps dispatching children.
    scorer = SubStringScorer(substring="never-in-the-response")
    return PromptSendingAttack(
        objective_target=target, attack_scoring_config=AttackScoringConfig(objective_scorer=scorer)
    )


def _adaptive_style_group(*, target: PromptTarget, name: str = "adaptive_objective") -> AtomicAttack:
    """One orchestration parent with two target-facing children, wired the way Adaptive wires them."""
    parent = SequentialAttack(
        objective_target=target,
        child_attacks=[
            SequentialChildAttack(
                strategy=_prompt_sending(target=target),
                seed_group=_SEED_GROUP,
                memory_labels={ADAPTIVE_ATTEMPT_LABEL: str(attempt)},
            )
            for attempt in (1, 2)
        ],
    )
    return AtomicAttack(
        atomic_attack_name=name,
        attack_technique=AttackTechnique(attack=parent),
        seed_groups=[_SEED_GROUP],
        group_kind=ScenarioRunPlanGroupKind.ADAPTIVE,
    )


async def _run_and_read_progress_async(
    *,
    memory: MemoryInterface,
    build_atomic_attacks: Callable[[PromptTarget], list[AtomicAttack]],
    target: PromptTarget,
    expect_partial_failure: bool = False,
) -> tuple[ScenarioProgressSnapshot, ScenarioRunPlan, str]:
    """Initialize and run a real scenario, then read its persisted plan and rows through the read model."""
    scenario = _RoleScenario(build_atomic_attacks=build_atomic_attacks)
    scenario.set_params_from_args(args={"objective_target": target})
    await scenario.initialize_async()
    if expect_partial_failure:
        with pytest.raises(ScenarioPartialFailureException):
            await scenario.run_async()
    else:
        await scenario.run_async()
    run_id = str(scenario._scenario_result_id)
    [stored] = await memory.get_scenario_results_async(scenario_result_ids=[run_id])
    plan = ScenarioRunPlan.model_validate(stored.metadata[SCENARIO_RUN_PLAN_METADATA_KEY])
    snapshot = await ScenarioProgressReadModel(memory=memory).get_snapshot_async(
        scenario_result_id=run_id,
        plan=plan,
        plan_complete=True,
        active_group_ids=(),
        terminal=True,
        objective_scorer_identifier=None,
    )
    return snapshot, plan, run_id


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioResultRoles:
    async def test_real_run_projects_roles_children_attempts_and_group_kinds_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        def build(target: PromptTarget) -> list[AtomicAttack]:
            return [
                build_baseline_atomic_attack(
                    objective_target=target,
                    objective_scorer=SubStringScorer(substring="never-in-the-response"),
                    seed_groups=[_SEED_GROUP],
                ),
                AtomicAttack(
                    atomic_attack_name="plain_attack",
                    attack_technique=AttackTechnique(attack=_prompt_sending(target=target)),
                    seed_groups=[_SEED_GROUP],
                ),
                _adaptive_style_group(target=target),
            ]

        snapshot, plan, _ = await _run_and_read_progress_async(
            memory=sqlite_instance, build_atomic_attacks=build, target=MockPromptTarget()
        )

        assert {group.atomic_attack_name: group.kind for group in plan.atomic_groups} == {
            "baseline": ScenarioRunPlanGroupKind.BASELINE,
            "plain_attack": ScenarioRunPlanGroupKind.ATTACK,
            "adaptive_objective": ScenarioRunPlanGroupKind.ADAPTIVE,
        }
        assert {group.atomic_attack_name: group.kind for group in snapshot.summary.atomic_groups} == {
            "baseline": ScenarioRunPlanGroupKind.BASELINE,
            "plain_attack": ScenarioRunPlanGroupKind.ATTACK,
            "adaptive_objective": ScenarioRunPlanGroupKind.ADAPTIVE,
        }

        by_name: dict[str, list[ScenarioProgressResult]] = {}
        for result in snapshot.results:
            by_name.setdefault(result.atomic_attack_name, []).append(result)
        for name in ("baseline", "plain_attack"):
            [result] = by_name[name]
            assert result.result_role is AttackResultRole.TARGET_FACING
            assert result.child_attack_result_ids == []
            assert result.attempt_index is None

        [parent] = [r for r in by_name["adaptive_objective"] if r.result_role is AttackResultRole.ORCHESTRATION]
        children = sorted(
            (r for r in by_name["adaptive_objective"] if r.result_role is AttackResultRole.TARGET_FACING),
            key=lambda r: r.attempt_index or 0,
        )
        assert parent.conversation_id == ""
        assert parent.attempt_index is None
        assert [child.attempt_index for child in children] == [1, 2]
        # The stored order survives the database round trip and matches the dispatch order.
        assert parent.child_attack_result_ids == [child.attack_result_id for child in children]
        assert all(child.child_attack_result_ids == [] for child in children)
        # Parent and children share one planned unit, as before this change.
        assert {r.atomic_group_id for r in (parent, *children)} == {parent.atomic_group_id}
        assert {r.seed_group_id for r in (parent, *children)} == {parent.seed_group_id}

        # Adaptive's attempt label is kept on each stored child result. These children sit directly
        # under the Adaptive parent, so the label agrees with their parent-relative index.
        stored_children = await sqlite_instance.get_attack_results_async(
            attack_result_ids=[child.attack_result_id for child in children]
        )
        assert {stored.attack_result_id: stored.labels[ADAPTIVE_ATTEMPT_LABEL] for stored in stored_children} == {
            child.attack_result_id: str(child.attempt_index) for child in children
        }

        # The REST wire format carries the contract as plain strings.
        wire_parent = parent.model_dump(mode="json")
        assert wire_parent["result_role"] == "orchestration"
        assert wire_parent["child_attack_result_ids"] == [child.attack_result_id for child in children]
        assert children[1].model_dump(mode="json")["attempt_index"] == 2
        assert {g["kind"] for g in snapshot.summary.model_dump(mode="json")["atomic_groups"]} == {
            "baseline",
            "attack",
            "adaptive",
        }

    async def test_failed_orchestration_parent_keeps_its_role_despite_having_a_conversation_id_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        snapshot, _, _ = await _run_and_read_progress_async(
            memory=sqlite_instance,
            build_atomic_attacks=lambda target: [_adaptive_style_group(target=target)],
            target=_UnavailableTarget(),
            expect_partial_failure=True,
        )

        by_role = {result.result_role: result for result in snapshot.results}
        assert set(by_role) == {AttackResultRole.ORCHESTRATION, AttackResultRole.TARGET_FACING}
        parent = by_role[AttackResultRole.ORCHESTRATION]
        child = by_role[AttackResultRole.TARGET_FACING]
        assert parent.outcome == child.outcome == AttackOutcome.ERROR
        # An error result gets a generated conversation ID, so an empty ID would misclassify it.
        assert parent.conversation_id != ""
        assert child.attempt_index == 1

    async def test_nested_compound_attempt_index_is_relative_to_its_own_parent_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        class _OrderedSelector:
            async def select_async(self, *, technique_identifiers, objective, num_top_techniques, scenario_result_id):
                return ["single", "nested"][:num_top_techniques]

        target = MockPromptTarget()
        nested_technique = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(strategy=_prompt_sending(target=target), seed_group=_SEED_GROUP) for _ in range(2)
            ],
        )
        dispatcher = AdaptiveTechniqueDispatcher(
            objective_target=target,
            techniques={
                "single": TechniqueBundle(attack=_prompt_sending(target=target), name="single"),
                "nested": TechniqueBundle(attack=nested_technique, name="nested"),
            },
            selector=_OrderedSelector(),
            max_attempts_per_objective=2,
        )
        adaptive_group = AtomicAttack(
            atomic_attack_name="adaptive_objective",
            attack_technique=AttackTechnique(attack=await dispatcher.build_attack_async(seed_group=_SEED_GROUP)),
            seed_groups=[_SEED_GROUP],
            group_kind=ScenarioRunPlanGroupKind.ADAPTIVE,
        )

        snapshot, _, _ = await _run_and_read_progress_async(
            memory=sqlite_instance, build_atomic_attacks=lambda _: [adaptive_group], target=target
        )

        by_id = {result.attack_result_id: result for result in snapshot.results}
        [outer] = [
            r for r in snapshot.results if r.result_role is AttackResultRole.ORCHESTRATION and r.attempt_index is None
        ]
        single_id, nested_id = outer.child_attack_result_ids
        single, nested = by_id[single_id], by_id[nested_id]
        nested_children = [by_id[child_id] for child_id in nested.child_attack_result_ids]

        assert single.result_role is AttackResultRole.TARGET_FACING
        assert nested.result_role is AttackResultRole.ORCHESTRATION
        assert [child.result_role for child in nested_children] == [AttackResultRole.TARGET_FACING] * 2
        assert (single.attempt_index, nested.attempt_index) == (1, 2)
        # Nested children count from 1 under their own parent, not under the Adaptive parent.
        assert [child.attempt_index for child in nested_children] == [1, 2]

        # Adaptive's label still names the outer attempt, so it differs from the nested children's index.
        stored = await sqlite_instance.get_attack_results_async(
            attack_result_ids=[single_id, *nested.child_attack_result_ids]
        )
        labels = {result.attack_result_id: result.labels[ADAPTIVE_ATTEMPT_LABEL] for result in stored}
        assert labels == {single_id: "1", **dict.fromkeys(nested.child_attack_result_ids, "2")}

    async def test_roles_preserve_unit_counts_but_remove_duplicate_retry_pressure_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        snapshot, plan, run_id = await _run_and_read_progress_async(
            memory=sqlite_instance,
            build_atomic_attacks=lambda target: [
                _adaptive_style_group(target=target, name="adaptive_a"),
                _adaptive_style_group(target=target, name="adaptive_b"),
            ],
            target=MockPromptTarget(),
        )
        legacy_deltas = [
            delta.model_copy(
                update={
                    "attribution_data": {
                        key: value
                        for key, value in delta.attribution_data.items()
                        if key not in ("result_role", "attempt_index")
                    },
                    "attack_metadata": {},
                }
            )
            for delta in snapshot.deltas
        ]
        legacy_memory = MagicMock(spec=MemoryInterface)
        legacy_memory.get_scenario_attack_result_deltas_async = AsyncMock(return_value=(legacy_deltas, False))

        legacy = await ScenarioProgressReadModel(memory=legacy_memory).get_snapshot_async(
            scenario_result_id=run_id,
            plan=plan,
            plan_complete=True,
            active_group_ids=(),
            terminal=True,
            objective_scorer_identifier=None,
        )

        assert {result.result_role for result in legacy.results} == {AttackResultRole.UNKNOWN}

        def _strip(obj):
            if isinstance(obj, dict):
                obj.pop("producer_counts", None)
                obj.pop("retries", None)
                for v in obj.values():
                    _strip(v)
            elif isinstance(obj, list):
                for item in obj:
                    _strip(item)
            return obj

        legacy_dump = _strip(legacy.summary.model_dump())
        snapshot_dump = _strip(snapshot.summary.model_dump())
        assert legacy_dump == snapshot_dump
        assert snapshot.summary.overall.planned == 2
        assert snapshot.summary.overall.retries == 2
        assert legacy.summary.overall.retries == 4


@pytest.mark.parametrize(
    ("attribution_data", "attack_metadata", "expected_role", "expected_children", "expected_attempt"),
    [
        # A legacy envelope: no recorded role and no conversation. Nothing is inferred.
        ({"parent_collection": "attack"}, {"child_attack_result_ids": ["c1", "c2"]}, "unknown", ["c1", "c2"], None),
        ({"parent_collection": "attack", "result_role": "a_future_role"}, {}, "unknown", [], None),
        ({"parent_collection": "attack", "result_role": ["not", "a", "string"]}, {}, "unknown", [], None),
        (
            {"parent_collection": "attack", "result_role": "target_facing", "attempt_index": 0},
            {},
            "target_facing",
            [],
            None,
        ),
        ({"parent_collection": "attack", "attempt_index": True}, {}, "unknown", [], None),
        ({"parent_collection": "attack", "attempt_index": "2"}, {"child_attack_result_ids": "c1"}, "unknown", [], None),
        ({"parent_collection": "attack"}, {"child_attack_result_ids": ["c1", 7]}, "unknown", [], None),
    ],
)
def test_map_progress_delta_reads_legacy_and_malformed_rows_conservatively(
    attribution_data: dict[str, object],
    attack_metadata: dict[str, object],
    expected_role: str,
    expected_children: list[str],
    expected_attempt: int | None,
) -> None:
    delta = ScenarioAttackResultDelta(
        attack_result_id="row",
        conversation_id="",
        objective="objective",
        outcome=AttackOutcome.FAILURE,
        execution_time_ms=1,
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        attribution_data=attribution_data,
        attack_metadata=attack_metadata,
    )

    mapped = ScenarioProgressReadModel._map_progress_delta(
        delta=delta, plan_lookup=ScenarioPlanLookup.from_plan(plan=None)
    )

    assert mapped.result_role.value == expected_role
    assert mapped.child_attack_result_ids == expected_children
    assert mapped.attempt_index == expected_attempt


async def test_legacy_plan_groups_read_as_unknown_kind_async() -> None:
    memory = MagicMock(spec=MemoryInterface)
    memory.get_scenario_attack_result_deltas_async = AsyncMock(return_value=([_make_delta(run_id="legacy")], False))
    stored_plan = {
        "version": 1,
        "atomic_groups": [
            {
                "id": "group",
                "atomic_attack_name": "attack",
                "display_group": "attack",
                "technique_eval_hash": "",
                "seed_group_ids": ["seed-0"],
            }
        ],
        "seed_groups": [{"id": "seed-0", "objective_sha256": "sha-legacy-0", "objective": "objective-legacy-0"}],
    }

    with_plan = await ScenarioProgressReadModel(memory=memory).get_snapshot_async(
        scenario_result_id="legacy",
        plan=ScenarioRunPlan.model_validate(stored_plan),
        plan_complete=True,
        active_group_ids=(),
        terminal=True,
        objective_scorer_identifier=None,
    )
    without_plan = await _get_snapshot_async(read_model=ScenarioProgressReadModel(memory=memory), run_id="legacy")

    # A stored legacy plan is re-saved without gaining a kind, and progress reports it as unknown.
    resaved = ScenarioRunPlan.model_validate(stored_plan).model_dump(mode="json", exclude_none=True)
    assert all("kind" not in group for group in resaved["atomic_groups"])
    assert [group.kind for group in with_plan.summary.atomic_groups] == [ScenarioRunPlanGroupKind.UNKNOWN]
    assert [group.kind for group in without_plan.summary.atomic_groups] == [ScenarioRunPlanGroupKind.UNKNOWN]


@pytest.mark.usefixtures("patch_central_database")
async def test_preparation_failure_stays_separate_from_result_role_async(sqlite_instance: MemoryInterface) -> None:
    scenario = make_scenario_result(attack_results={}, objective_target_identifier=get_mock_target_identifier())
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scenario])
    failure = AttackPreparationFailure(kind=AttackPreparationFailureKind.ADVERSARIAL_CHAT_REFUSED, reason="refused")
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            AttackResult(
                conversation_id="conversation",
                objective="objective",
                outcome=AttackOutcome.UNDETERMINED,
                outcome_reason="refused",
                metadata=failure.to_metadata(),
                attribution_parent_id=str(scenario.id),
                attribution_data={"parent_collection": "attack", "result_role": "target_facing"},
            )
        ]
    )

    [delta], _ = await sqlite_instance.get_scenario_attack_result_deltas_async(
        scenario_result_id=str(scenario.id), cursor=None, limit=10
    )
    mapped = ScenarioProgressReadModel._map_progress_delta(
        delta=delta, plan_lookup=ScenarioPlanLookup.from_plan(plan=None)
    )
    [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[delta.attack_result_id])

    # The role describes the record, not whether the target was reached; the failure signal is unchanged.
    assert mapped.result_role is AttackResultRole.TARGET_FACING
    assert mapped.outcome is AttackOutcome.UNDETERMINED
    assert mapped.child_attack_result_ids == []
    assert AttackPreparationFailure.from_result(result=stored) == failure


async def test_get_snapshot_replaces_and_reorders_reemitted_delta_async() -> None:
    memory = MagicMock(spec=MemoryInterface)
    before = _make_delta(run_id="run-state", index=-1)
    delta = _make_delta(run_id="run-state", index=0)
    after = _make_delta(run_id="run-state", index=1)
    bumped = delta.model_copy(
        update={"outcome": AttackOutcome.FAILURE, "timestamp": delta.timestamp + timedelta(seconds=99)}
    )
    memory.get_scenario_attack_result_deltas_async = AsyncMock(
        side_effect=[([before, delta, after], False), ([bumped], False), ([], False)]
    )
    read_model = ScenarioProgressReadModel(memory=memory)

    first = await _get_snapshot_async(read_model=read_model, run_id="run-state")
    second = await _get_snapshot_async(read_model=read_model, run_id="run-state")
    unchanged = await _get_snapshot_async(read_model=read_model, run_id="run-state")

    assert first.deltas == (before, delta, after)
    assert first.results[1].outcome == AttackOutcome.SUCCESS
    assert first.summary.overall.succeeded == 3
    assert second.deltas == (before, after, bumped)
    assert [result.attack_result_id for result in second.results] == [
        before.attack_result_id,
        after.attack_result_id,
        delta.attack_result_id,
    ]
    assert second.results[0] is first.results[0]
    assert second.results[-1].outcome == AttackOutcome.FAILURE
    assert second.summary.overall.succeeded == 2
    assert second.summary.overall.retries == 0
    assert unchanged.results == second.results
    assert unchanged.summary is second.summary


async def test_get_snapshot_orders_replacements_and_new_rows_across_pages_async() -> None:
    memory = MagicMock(spec=MemoryInterface)
    deltas = [
        _make_delta(run_id="run-pages", index=index).model_copy(
            update={"attack_result_id": str(uuid.UUID(int=result_id))}
        )
        for index, result_id in enumerate((1, 3, 4))
    ]
    timestamp = deltas[-1].timestamp + timedelta(seconds=1)
    first_bumped, second_bumped = [delta.model_copy(update={"timestamp": timestamp}) for delta in deltas[:2]]
    appended = first_bumped.model_copy(update={"attack_result_id": str(uuid.UUID(int=2))})
    memory.get_scenario_attack_result_deltas_async = AsyncMock(
        side_effect=[
            (deltas, False),
            ([first_bumped, appended], True),
            ([second_bumped], False),
        ]
    )
    read_model = ScenarioProgressReadModel(memory=memory)
    await _get_snapshot_async(read_model=read_model, run_id="run-pages")
    snapshot = await _get_snapshot_async(read_model=read_model, run_id="run-pages")

    assert snapshot.deltas == (deltas[2], first_bumped, appended, second_bumped)
    assert [result.attack_result_id for result in snapshot.results] == [
        delta.attack_result_id for delta in snapshot.deltas
    ]
    assert memory.get_scenario_attack_result_deltas_async.call_args.kwargs["cursor"] == AttackResultKeysetCursor(
        timestamp=timestamp, attack_result_id=appended.attack_result_id
    )


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_get_snapshot_recovers_reordered_replacement_after_interrupted_refresh_async(
    error_type: type[BaseException],
) -> None:
    memory = MagicMock(spec=MemoryInterface)
    first = _make_delta(run_id="run-interrupted", index=0)
    second = _make_delta(run_id="run-interrupted", index=1)
    bumped = first.model_copy(
        update={"timestamp": second.timestamp + timedelta(seconds=1), "outcome": AttackOutcome.FAILURE}
    )
    memory.get_scenario_attack_result_deltas_async = AsyncMock(
        side_effect=[([first, second], False), ([bumped], True), error_type(), ([], False)]
    )
    read_model = ScenarioProgressReadModel(memory=memory)
    initial = await _get_snapshot_async(read_model=read_model, run_id="run-interrupted")

    with pytest.raises(error_type):
        await _get_snapshot_async(read_model=read_model, run_id="run-interrupted")
    assert not read_model._refresh_states
    refreshed = await _get_snapshot_async(read_model=read_model, run_id="run-interrupted")

    assert initial.deltas == (first, second)
    assert initial.summary.overall.succeeded == 2
    assert refreshed.deltas == (second, bumped)
    assert [result.outcome for result in refreshed.results] == [AttackOutcome.SUCCESS, AttackOutcome.FAILURE]
    assert refreshed.summary.overall.succeeded == 1
    assert memory.get_scenario_attack_result_deltas_async.call_args.kwargs["cursor"] == AttackResultKeysetCursor(
        timestamp=bumped.timestamp, attack_result_id=bumped.attack_result_id
    )
    assert not read_model._refresh_states


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("limit", [1, 500])
async def test_progress_pages_keep_all_results_after_real_attack_edit_async(
    *, sqlite_instance: MemoryInterface, limit: int
) -> None:
    scenario = make_scenario_result(
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
        scenario_run_state=ScenarioRunState.COMPLETED,
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scenario])
    rows = [
        AttackResult(
            attack_result_id=str(uuid.UUID(int=index + 1)),
            conversation_id=str(uuid.uuid4()),
            objective=f"describe everyday object {index}",
            outcome=AttackOutcome.FAILURE,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
            attribution_parent_id=str(scenario.id),
            attribution_data={"parent_collection": "attack", "seed_group_id": f"seed-{index}"},
        )
        for index in range(limit + 1)
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=rows)
    service = ScenarioRunService()
    try:
        initial = await service.get_run_progress_async(scenario_result_id=str(scenario.id), since=None, limit=500)
        assert initial is not None
        initial_payload = initial.model_dump(mode="json")
        edited = rows[limit - 1]
        updated = await AttackService().update_attack_async(
            attack_result_id=edited.attack_result_id, request=UpdateAttackRequest(outcome="success")
        )
        assert updated is not None

        first_page = await service.get_run_progress_async(scenario_result_id=str(scenario.id), since=None, limit=limit)
        assert first_page is not None
        expected_ids = [row.attack_result_id for row in rows if row is not edited] + [edited.attack_result_id]
        assert [result.attack_result_id for result in first_page.results] == expected_ids[:limit]
        assert first_page.has_more
        last_page = await service.get_run_progress_async(
            scenario_result_id=str(scenario.id), since=first_page.next_cursor, limit=limit
        )
        assert last_page is not None
        assert [result.attack_result_id for result in last_page.results] == expected_ids[limit:]
        assert last_page.results[0].outcome == AttackOutcome.SUCCESS
        assert not last_page.has_more
        assert last_page.summary.overall.completed == limit + 1
        assert last_page.summary.overall.succeeded == 1
        assert last_page.summary.overall.retries == 0
        assert last_page.summary.overall.producer_counts.unknown.attempts == limit + 1

        replay = await service.get_run_progress_async(
            scenario_result_id=str(scenario.id), since=first_page.next_cursor, limit=limit
        )
        assert replay is not None
        assert replay.results == last_page.results
        assert replay.next_cursor == last_page.next_cursor
        empty = await service.get_run_progress_async(
            scenario_result_id=str(scenario.id), since=last_page.next_cursor, limit=limit
        )
        assert empty is not None
        assert empty.results == []
        assert not empty.has_more
        assert empty.next_cursor == last_page.next_cursor
        assert initial.model_dump(mode="json") == initial_payload
    finally:
        await service.close_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_progress_latest_edited_attempt_matches_sdk_and_cold_cache_async(
    sqlite_instance: MemoryInterface,
) -> None:
    scenario = make_scenario_result(
        attack_results={},
        objective_target_identifier=get_mock_target_identifier(),
        scenario_run_state=ScenarioRunState.COMPLETED,
    )
    await sqlite_instance.add_scenario_results_to_memory_async(scenario_results=[scenario])
    rows = [
        AttackResult(
            conversation_id=str(uuid.uuid4()),
            objective="describe an everyday object",
            outcome=AttackOutcome.FAILURE,
            timestamp=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
            attribution_parent_id=str(scenario.id),
            attribution_data={"parent_collection": "attack", "seed_group_id": "seed"},
        )
        for index in range(2)
    ]
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=rows)
    warm_service = ScenarioRunService()
    cold_service = ScenarioRunService()
    try:
        initial = await warm_service.get_run_progress_async(scenario_result_id=str(scenario.id), since=None, limit=500)
        assert initial is not None
        assert initial.summary.overall.succeeded == 0
        updated = await AttackService().update_attack_async(
            attack_result_id=rows[0].attack_result_id, request=UpdateAttackRequest(outcome="success")
        )
        assert updated is not None
        warm = await warm_service.get_run_progress_async(
            scenario_result_id=str(scenario.id), since=initial.next_cursor, limit=500
        )
        cold = await cold_service.get_run_progress_async(scenario_result_id=str(scenario.id), since=None, limit=500)
        [stored] = await sqlite_instance.get_scenario_results_async(scenario_result_ids=[str(scenario.id)])
        sdk = compute_scenario_statistics(stored)

        assert warm is not None and cold is not None
        assert [result.attack_result_id for result in warm.results] == [rows[0].attack_result_id]
        assert [result.attack_result_id for result in cold.results] == [
            rows[1].attack_result_id,
            rows[0].attack_result_id,
        ]
        assert warm.summary == cold.summary
        assert warm.summary.overall == sdk.overall
        assert warm.summary.overall.completed == 1
        assert warm.summary.overall.succeeded == 1
        assert warm.summary.overall.retries == 1
        assert warm.summary.overall.producer_counts.unknown.attempts == 2
    finally:
        await warm_service.close_async()
        await cold_service.close_async()
