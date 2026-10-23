# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import inspect
from typing import assert_type
from unittest.mock import MagicMock, patch

import pytest

from unit.async_utils import (
    TEST_WATCHDOG_SECONDS,
    get_defined_tasks,
    settle_tasks_async,
    wait_for_completion_async,
    wait_for_event_async,
)


def test_get_defined_tasks_before_task_creation() -> None:
    assert get_defined_tasks(None, None) == []


async def test_get_defined_tasks_preserves_tasks_and_order_async() -> None:
    async def complete_async(value: int) -> int:
        return value

    first = asyncio.create_task(complete_async(1))
    second = asyncio.create_task(complete_async(2))
    tasks = get_defined_tasks(None, first, None, second)
    assert_type(tasks, list[asyncio.Task[int]])
    assert tasks == [first, second]
    assert await asyncio.gather(*tasks) == [1, 2]


async def test_wait_for_completion_returns_result_async() -> None:
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    future.set_result(42)

    assert await wait_for_completion_async(future=future) == 42


async def test_wait_for_completion_preserves_exception_async() -> None:
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    failure = RuntimeError("operation failed")
    future.set_exception(failure)

    with pytest.raises(RuntimeError, match="operation failed") as error:
        await wait_for_completion_async(future=future)
    assert error.value is failure


async def test_wait_for_completion_preserves_cancellation_async() -> None:
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    future.cancel("requested cancellation")

    with pytest.raises(asyncio.CancelledError, match="requested cancellation"):
        await wait_for_completion_async(future=future)


def test_wait_for_completion_defaults_to_a_backstop_timeout() -> None:
    # A default low enough to be reached under CI load turns the watchdog into a latency
    # assertion, failing unrelated tests with TimeoutError instead of catching a deadlock.
    parameter = inspect.signature(wait_for_completion_async).parameters["timeout"]
    assert parameter.default == 300


async def test_wait_for_completion_timeout_does_not_cancel_task_async() -> None:
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    try:
        with pytest.raises(TimeoutError, match="test watchdog expired"):
            await wait_for_completion_async(future=task, timeout=0)
        assert not task.done()
        assert task.cancelling() == 0

        release.set()
        assert await wait_for_completion_async(future=task)
    finally:
        release.set()
        await task


async def test_wait_for_event_observes_checkpoint_before_completion_async() -> None:
    reached = asyncio.Event()
    release = asyncio.Event()

    async def operation_async() -> None:
        reached.set()
        await release.wait()

    task = asyncio.create_task(operation_async())
    before = asyncio.all_tasks()
    try:
        await wait_for_event_async(event=reached, tasks=[task])
        assert not task.done()
        assert task.cancelling() == 0
        assert asyncio.all_tasks() == before
    finally:
        release.set()
        await wait_for_completion_async(future=task)


async def test_wait_for_event_observes_already_reached_checkpoint_with_zero_timeout_async() -> None:
    event = asyncio.Event()
    event.set()
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    await wait_for_event_async(event=event, tasks=[future], timeout=0)
    assert not future.done()
    future.set_result(None)


async def test_wait_for_event_accepts_completion_after_checkpoint_async() -> None:
    event = asyncio.Event()

    async def operation_async() -> None:
        event.set()

    task = asyncio.create_task(operation_async())
    await wait_for_event_async(event=event, tasks=[task])
    await task


@pytest.mark.parametrize("cancel_operation", [False, True], ids=["error", "cancellation"])
async def test_wait_for_event_leaves_failure_at_checkpoint_for_caller_async(cancel_operation: bool) -> None:
    event = asyncio.Event()
    event.set()
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    if cancel_operation:
        future.cancel("operation failed")
        expected_error = asyncio.CancelledError
    else:
        future.set_exception(RuntimeError("operation failed"))
        expected_error = RuntimeError

    await wait_for_event_async(event=event, tasks=[future])
    with pytest.raises(expected_error, match="operation failed"):
        await wait_for_completion_async(future=future)


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_wait_for_event_reports_early_completion_async(outcome: str) -> None:
    event = asyncio.Event()
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    if outcome == "success":
        future.set_result(None)
        expected_error = AssertionError
        message = "without signalling readiness"
    elif outcome == "error":
        future.set_exception(RuntimeError("startup failed"))
        expected_error = RuntimeError
        message = "startup failed"
    else:
        future.cancel("operation cancelled")
        expected_error = asyncio.CancelledError
        message = "operation cancelled"

    before = asyncio.all_tasks()
    with pytest.raises(expected_error, match=message):
        await wait_for_event_async(event=event, tasks=[future])
    assert asyncio.all_tasks() == before
    assert not event.is_set()


@pytest.mark.parametrize("cancel_observer", [False, True], ids=["expired-watchdog", "cancelled-observer"])
async def test_wait_for_event_does_not_cancel_operation_async(cancel_observer: bool) -> None:
    event = asyncio.Event()
    release = asyncio.Event()
    operation = asyncio.create_task(release.wait())
    before = asyncio.all_tasks()
    try:
        if cancel_observer:
            observer = asyncio.create_task(wait_for_event_async(event=event, tasks=[operation]))
            asyncio.get_running_loop().call_soon(observer.cancel, "observer cancelled")
            with pytest.raises(asyncio.CancelledError, match="observer cancelled"):
                await observer
        else:
            with pytest.raises(TimeoutError, match="test watchdog expired"):
                await wait_for_event_async(event=event, tasks=[operation], timeout=0)
        assert not operation.done()
        assert operation.cancelling() == 0
        assert asyncio.all_tasks() == before
    finally:
        release.set()
        await wait_for_completion_async(future=operation)


async def test_settle_tasks_joins_peers_after_failure_async() -> None:
    release = asyncio.Event()
    finished = asyncio.Event()

    async def fail_async() -> None:
        raise RuntimeError("operation failed")

    async def peer_async() -> None:
        await release.wait()
        finished.set()

    failed, peer = asyncio.create_task(fail_async()), asyncio.create_task(peer_async())
    release.set()
    await settle_tasks_async(failed, peer)
    assert failed.done() and peer.done()
    assert finished.is_set()


@pytest.mark.timeout(TEST_WATCHDOG_SECONDS)
async def test_settle_tasks_watchdog_drains_cleanup_before_patch_teardown_async() -> None:
    started, cleanup_started, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    resource = MagicMock()

    async def blocked_async() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            resource.close()

    task = asyncio.create_task(blocked_async())
    settlement: asyncio.Task[None] | None = None
    with patch.object(resource, "close") as close:
        try:
            await wait_for_event_async(event=started, tasks=[task])
            settlement = asyncio.create_task(settle_tasks_async(task, timeout=0))
            await wait_for_event_async(event=cleanup_started, tasks=[settlement])
            assert not task.done()
            assert not settlement.done()
            close.assert_not_called()
            release_cleanup.set()
            with pytest.raises(TimeoutError, match="test watchdog expired"):
                await wait_for_completion_async(future=settlement)
            assert task.cancelled()
            close.assert_called_once()
        finally:
            release_cleanup.set()
            task.cancel()
            await settle_tasks_async(*get_defined_tasks(task, settlement))


async def test_wait_for_event_reports_readiness_async() -> None:
    event = asyncio.Event()
    event.set()
    await wait_for_event_async(event=event, tasks=[])


@pytest.mark.parametrize("complete_before_wait", [False, True])
async def test_wait_for_event_readiness_leaves_completed_failure_for_caller_async(
    *, complete_before_wait: bool
) -> None:
    event = asyncio.Event()
    failure = OSError("operation failed after readiness")

    async def signal_and_fail_async() -> None:
        event.set()
        raise failure

    task = asyncio.create_task(signal_and_fail_async())
    try:
        if complete_before_wait:
            await asyncio.wait({task})
        await wait_for_event_async(event=event, tasks=[task])
        assert task.done()
        with pytest.raises(OSError, match="failed after readiness") as error:
            await wait_for_completion_async(future=task)
        assert error.value is failure
    finally:
        await settle_tasks_async(task)
