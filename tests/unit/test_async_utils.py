# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import inspect
from typing import assert_type

import pytest

from unit.async_utils import get_defined_tasks, wait_for_completion_async, wait_for_event_async


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
    try:
        await wait_for_event_async(event=reached, task=task)
        assert not task.done()
        assert task.cancelling() == 0
    finally:
        release.set()
        await wait_for_completion_async(future=task)


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_wait_for_event_reports_early_completion_async(outcome: str) -> None:
    event = asyncio.Event()
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    if outcome == "success":
        future.set_result(None)
        expected_error = AssertionError
        message = "finished before reaching the expected event"
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
        await wait_for_event_async(event=event, task=future)
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
            observer = asyncio.create_task(wait_for_event_async(event=event, task=operation))
            asyncio.get_running_loop().call_soon(observer.cancel, "observer cancelled")
            with pytest.raises(asyncio.CancelledError, match="observer cancelled"):
                await observer
        else:
            with pytest.raises(TimeoutError, match="test watchdog expired"):
                await wait_for_event_async(event=event, task=operation, timeout=0)
        assert not operation.done()
        assert operation.cancelling() == 0
        assert asyncio.all_tasks() == before
    finally:
        release.set()
        await wait_for_completion_async(future=operation)
