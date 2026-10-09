# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import gc
import inspect
import threading
import weakref
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from pyrit.analytics._execution import AnalyticsExecution
from pyrit.common.task_utils import gather_with_cleanup_async
from pyrit.exceptions.analytics_exception import AnalyticsBusyException, AnalyticsTimeoutException

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine

    from pyrit.memory.query_control import QueryControl


class _Blocked:
    def __init__(self, *, result: str = "result", error: Exception | None = None) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exited = asyncio.Event()
        self.control: QueryControl | None = None
        self.result = result
        self.error = error
        self.cancellations = 0

    async def run_async(self, control: QueryControl) -> str:
        self.control = control
        self.entered.set()
        try:
            await self.release.wait()
            if self.error is not None:
                raise self.error
            return self.result
        except asyncio.CancelledError:
            self.cancellations += 1
            raise
        finally:
            self.exited.set()


class _Harness:
    def __init__(self) -> None:
        self.executions: list[AnalyticsExecution] = []
        self.operations: list[_Blocked] = []
        self.callers: list[asyncio.Task[str]] = []

    def controller(self, **overrides: int | float) -> AnalyticsExecution:
        options = {
            "report_workers": 1,
            "quick_workers": 1,
            "max_queue": 1,
            "queue_timeout": 10.0,
            "report_timeout": 10.0,
            "quick_timeout": 10.0,
            **overrides,
        }
        execution = AnalyticsExecution(**options)
        self.executions.append(execution)
        return execution

    def blocked(self, *, result: str = "result", error: Exception | None = None) -> _Blocked:
        operation = _Blocked(result=result, error=error)
        self.operations.append(operation)
        return operation

    def submit(self, *, execution: AnalyticsExecution, operation: _Blocked, report: bool = True) -> asyncio.Task[str]:
        caller = asyncio.create_task(execution.run_async(report=report, task=operation.run_async))
        self.callers.append(caller)
        return caller


@pytest.fixture
async def harness() -> AsyncGenerator[_Harness, None]:
    harness = _Harness()
    try:
        yield harness
    finally:
        for operation in harness.operations:
            operation.release.set()
        await gather_with_cleanup_async(execution.close_async() for execution in harness.executions)
        await asyncio.gather(*harness.callers, return_exceptions=True)


async def test_defaults_bound_both_lanes() -> None:
    execution = AnalyticsExecution()
    assert (execution._lanes[True].limit, execution._lanes[False].limit) == (5, 2)
    assert (execution._max_queue, execution._queue_timeout) == (10, 1)
    assert (execution._lanes[True].timeout, execution._lanes[False].timeout) == (5, 1)
    await execution.close_async()


@pytest.mark.parametrize("name", ["report_workers", "quick_workers", "max_queue"])
@pytest.mark.parametrize("value", [0, -1, 1.5, True, float("inf"), float("nan"), "1", None])
async def test_invalid_counts_are_rejected(*, name: str, value: object) -> None:
    with pytest.raises(ValueError, match=name):
        AnalyticsExecution(**{name: value})


@pytest.mark.parametrize("name", ["queue_timeout", "report_timeout", "quick_timeout"])
@pytest.mark.parametrize("value", [0, -1.0, True, float("inf"), float("nan"), "1", None, 10**400])
async def test_invalid_timeouts_are_rejected(*, name: str, value: object) -> None:
    with pytest.raises(ValueError, match=name):
        AnalyticsExecution(**{name: value})


@pytest.mark.parametrize("report", [True, False])
async def test_work_uses_caller_loop_and_returns_independent_results(harness: _Harness, report: bool) -> None:
    execution = harness.controller()
    loop, thread = asyncio.get_running_loop(), threading.get_ident()

    async def work_async(control: QueryControl) -> list[int]:
        assert asyncio.get_running_loop() is loop
        assert threading.get_ident() == thread
        control.check()
        return [1]

    first = await execution.run_async(report=report, task=work_async)
    second = await execution.run_async(report=report, task=work_async)
    assert first == second
    assert first is not second


@pytest.mark.parametrize("error", [ValueError("query failed"), AnalyticsBusyException(), AnalyticsTimeoutException()])
async def test_failures_propagate_and_release_capacity(harness: _Harness, error: Exception) -> None:
    execution = harness.controller()
    operation = harness.blocked(error=error)
    operation.release.set()
    with pytest.raises(type(error)):
        await execution.run_async(report=True, task=operation.run_async)
    assert execution._lanes[True].active == 0
    successful = harness.blocked()
    successful.release.set()
    assert await execution.run_async(report=True, task=successful.run_async) == "result"


@pytest.mark.parametrize("report", [True, False])
async def test_failed_task_scheduling_releases_slot_and_closes_coroutine(report: bool) -> None:
    execution = AnalyticsExecution(report_workers=1, quick_workers=1)
    rejected: list[Coroutine[object, object, object]] = []
    operation = _Blocked()
    operation.release.set()

    def reject(coroutine: Coroutine[object, object, object]) -> None:
        rejected.append(coroutine)
        raise RuntimeError("Task scheduling failed")

    try:
        with patch.object(asyncio.get_running_loop(), "create_task", side_effect=reject):
            with pytest.raises(RuntimeError, match="Task scheduling failed"):
                await execution.run_async(report=report, task=operation.run_async)
        assert rejected
        assert all(inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED for coroutine in rejected)
        assert not operation.entered.is_set()
        assert execution._lanes[report].active == 0
        assert not execution._lanes[report].running
        assert await execution.run_async(report=report, task=operation.run_async) == "result"
    finally:
        for coroutine in rejected:
            coroutine.close()
        if not execution._lanes[report].running:
            await execution.close_async()


async def test_lanes_reserve_independent_running_and_queued_capacity(harness: _Harness) -> None:
    execution = harness.controller()
    report, quick, queued_report, queued_quick = [harness.blocked() for _ in range(4)]
    report_call = harness.submit(execution=execution, operation=report)
    quick_call = harness.submit(execution=execution, operation=quick, report=False)
    await gather_with_cleanup_async([report.entered.wait(), quick.entered.wait()])
    waiting_report = harness.submit(execution=execution, operation=queued_report)
    waiting_quick = harness.submit(execution=execution, operation=queued_quick, report=False)
    await asyncio.sleep(0)
    for lane in (True, False):
        with pytest.raises(AnalyticsBusyException):
            await execution.run_async(report=lane, task=harness.blocked().run_async)
    quick.release.set()
    await queued_quick.entered.wait()
    assert not queued_report.entered.is_set()
    assert not report_call.done()
    queued_quick.release.set()
    assert await quick_call == await waiting_quick == "result"
    report.release.set()
    await queued_report.entered.wait()
    queued_report.release.set()
    assert await report_call == await waiting_report == "result"


async def test_admission_is_fifo_with_new_arrivals(harness: _Harness) -> None:
    execution = harness.controller(max_queue=2)
    active, first, second, newcomer = [harness.blocked() for _ in range(4)]
    harness.submit(execution=execution, operation=active)
    await active.entered.wait()
    harness.submit(execution=execution, operation=first)
    harness.submit(execution=execution, operation=second)
    await asyncio.sleep(0)
    active.release.set()
    await first.entered.wait()
    harness.submit(execution=execution, operation=newcomer)
    await asyncio.sleep(0)
    first.release.set()
    await second.entered.wait()
    assert not newcomer.entered.is_set()
    second.release.set()
    await newcomer.entered.wait()


@pytest.mark.parametrize("report", [True, False])
async def test_queue_expiry_never_starts_database_work(harness: _Harness, report: bool) -> None:
    execution = harness.controller(queue_timeout=0.01)
    active, queued = harness.blocked(), harness.blocked()
    harness.submit(execution=execution, operation=active, report=report)
    await active.entered.wait()
    with pytest.raises(AnalyticsBusyException):
        await execution.run_async(report=report, task=queued.run_async)
    assert not queued.entered.is_set()
    assert not execution._lanes[report].queued
    assert execution._lanes[report].active == 1


@pytest.mark.parametrize("report", [True, False])
async def test_cancelling_queued_call_reclaims_only_its_queue_entry(harness: _Harness, report: bool) -> None:
    execution = harness.controller()
    active, queued, replacement = [harness.blocked() for _ in range(3)]
    harness.submit(execution=execution, operation=active, report=report)
    await active.entered.wait()
    caller = harness.submit(execution=execution, operation=queued, report=report)
    await asyncio.sleep(0)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not queued.entered.is_set()
    assert not active.control.expired
    harness.submit(execution=execution, operation=replacement, report=report)
    await asyncio.sleep(0)
    assert len(execution._lanes[report].queued) == 1
    active.release.set()
    await replacement.entered.wait()


@pytest.mark.parametrize("cancel_caller", [False, True], ids=["deadline", "cancellation"])
async def test_response_exit_keeps_slot_until_operation_exit(harness: _Harness, cancel_caller: bool) -> None:
    execution = harness.controller(report_timeout=10 if cancel_caller else 0.01)
    active, queued = harness.blocked(), harness.blocked()
    caller = harness.submit(execution=execution, operation=active)
    await active.entered.wait()
    if cancel_caller:
        caller.cancel()
    with pytest.raises(asyncio.CancelledError if cancel_caller else AnalyticsTimeoutException):
        await caller
    assert active.control.cancel_event.is_set()
    assert not active.exited.is_set()
    assert active.cancellations == 0
    execution._lanes[True].timeout = 10
    harness.submit(execution=execution, operation=queued)
    await asyncio.sleep(0)
    assert not queued.entered.is_set()
    with pytest.raises(AnalyticsBusyException):
        await execution.run_async(report=True, task=harness.blocked().run_async)
    active.release.set()
    await queued.entered.wait()
    assert active.exited.is_set()


async def test_expired_control_cannot_return_a_late_success(harness: _Harness) -> None:
    execution = harness.controller()
    operation = harness.blocked()
    caller = harness.submit(execution=execution, operation=operation)
    await operation.entered.wait()
    operation.control.deadline = 0
    operation.release.set()
    with pytest.raises(AnalyticsTimeoutException):
        await caller


async def test_operation_stays_strongly_owned_after_caller_leaves(harness: _Harness) -> None:
    execution = harness.controller()
    entered = asyncio.Event()
    gate_ref: weakref.ReferenceType[asyncio.Future[None]] | None = None

    async def wait_async(control: QueryControl) -> None:
        nonlocal gate_ref
        gate = asyncio.get_running_loop().create_future()
        gate_ref = weakref.ref(gate)
        entered.set()
        await gate

    caller = asyncio.create_task(execution.run_async(report=True, task=wait_async))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    del caller
    gc.collect()
    assert gate_ref is not None
    gate = gate_ref()
    assert gate is not None
    gate.set_result(None)
    await execution.close_async()
    assert execution._lanes[True].active == 0


async def test_cancellation_after_admission_grant_returns_reserved_slot(harness: _Harness) -> None:
    execution = harness.controller()
    lane = execution._lanes[True]
    lane.active = 1
    admission = asyncio.create_task(execution._admit_async(lane))
    await asyncio.sleep(0)
    execution._release(lane)
    admission.cancel()
    with pytest.raises(asyncio.CancelledError):
        await admission
    assert lane.active == 0
    assert not lane.queued


@pytest.mark.parametrize("report", [True, False])
async def test_cancellation_racing_with_slot_grant_never_starts_queued_work(harness: _Harness, report: bool) -> None:
    execution = harness.controller()
    active, queued = harness.blocked(), harness.blocked()
    queued.release.set()
    active_caller = harness.submit(execution=execution, operation=active, report=report)
    await active.entered.wait()
    queued_caller = harness.submit(execution=execution, operation=queued, report=report)
    await asyncio.sleep(0)
    lane = execution._lanes[report]
    assert len(lane.queued) == 1
    work = next(iter(lane.running))
    work.task.add_done_callback(lambda _: queued_caller.cancel())
    active.release.set()
    assert await active_caller == "result"
    with pytest.raises(asyncio.CancelledError):
        await queued_caller
    assert not queued.entered.is_set()
    assert lane.active == 0
    assert not lane.queued


@pytest.mark.parametrize("expire_before_grant", [False, True])
async def test_admission_rechecks_deadline_even_without_timer_callback(
    harness: _Harness, expire_before_grant: bool
) -> None:
    execution = harness.controller()
    lane = execution._lanes[True]
    lane.active = 1
    admission = asyncio.create_task(execution._admit_async(lane))
    await asyncio.sleep(0)
    deadline = lane.queued[0].deadline
    if not expire_before_grant:
        execution._release(lane)
    with patch("pyrit.analytics._execution.monotonic", return_value=deadline + 1):
        if expire_before_grant:
            execution._release(lane)
        with pytest.raises(AnalyticsBusyException):
            await admission
    assert lane.active == 0
    assert not lane.queued


async def test_unexpected_failure_after_cancellation_is_logged(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    execution = harness.controller()
    operation = harness.blocked(error=ValueError("cleanup failed"))
    caller = harness.submit(execution=execution, operation=operation)
    await operation.entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    operation.release.set()
    await execution.close_async()
    assert "Analytics operation failed after its caller left" in caplog.text
    assert "cleanup failed" in caplog.text


async def test_failure_is_logged_when_completion_precedes_caller_cancellation(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    execution = harness.controller()
    operation = harness.blocked(error=ValueError("query failed at cancellation"))
    caller = harness.submit(execution=execution, operation=operation)
    await operation.entered.wait()
    work = next(iter(execution._lanes[True].running))
    work.task.add_done_callback(lambda _: caller.cancel())
    operation.release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await execution.close_async()
    assert caplog.text.count("Analytics operation failed after its caller left") == 1
    assert "query failed at cancellation" in caplog.text


async def test_failed_close_scheduling_can_be_retried_without_reopening_admission(harness: _Harness) -> None:
    execution = harness.controller()
    operation = harness.blocked()
    caller = harness.submit(execution=execution, operation=operation)
    await operation.entered.wait()
    rejected: list[Coroutine[object, object, object]] = []

    def reject(coroutine: Coroutine[object, object, object]) -> None:
        rejected.append(coroutine)
        raise RuntimeError("Shutdown scheduling failed")

    try:
        with patch.object(asyncio.get_running_loop(), "create_task", side_effect=reject):
            with pytest.raises(RuntimeError, match="Shutdown scheduling failed"):
                await execution.close_async()
        assert rejected
        assert all(inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED for coroutine in rejected)
        assert not execution.is_closed
        assert operation.control.cancel_event.is_set()
        assert not operation.exited.is_set()
        with pytest.raises(AnalyticsBusyException):
            await execution.run_async(report=True, task=operation.run_async)
    finally:
        for coroutine in rejected:
            coroutine.close()
        operation.release.set()
        await execution.close_async()
    with pytest.raises(AnalyticsTimeoutException):
        await caller


async def test_close_rejects_queued_work_and_drains_through_repeated_cancellation(harness: _Harness) -> None:
    execution = harness.controller()
    active, queued = harness.blocked(), harness.blocked()
    caller = harness.submit(execution=execution, operation=active)
    await active.entered.wait()
    waiting = harness.submit(execution=execution, operation=queued)
    await asyncio.sleep(0)
    closing = asyncio.create_task(execution.close_async())
    await asyncio.sleep(0)
    with pytest.raises(AnalyticsBusyException):
        await waiting
    with pytest.raises(AnalyticsBusyException):
        await execution.run_async(report=False, task=queued.run_async)
    assert active.control.cancel_event.is_set()
    assert not queued.entered.is_set()
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    assert not execution.is_closed
    active.release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    with pytest.raises(AnalyticsTimeoutException):
        await caller
    assert execution.is_closed
    assert execution._lanes[True].active == 0
    await execution.close_async()
    with pytest.raises(AnalyticsBusyException):
        await execution.run_async(report=True, task=queued.run_async)


async def test_foreign_loop_cannot_use_or_close_live_controller(harness: _Harness) -> None:
    execution = harness.controller()
    operation = harness.blocked()
    operation.release.set()

    async def foreign_loop_async() -> None:
        with pytest.raises(RuntimeError, match="owning event loop"):
            await execution.run_async(report=True, task=operation.run_async)
        with pytest.raises(RuntimeError, match="owning event loop"):
            await execution.close_async()

    await asyncio.to_thread(asyncio.run, foreign_loop_async())
    assert not execution.is_closed
    assert await execution.run_async(report=True, task=operation.run_async) == "result"
