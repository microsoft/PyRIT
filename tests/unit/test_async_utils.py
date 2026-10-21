# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import inspect
from typing import assert_type
from unittest.mock import patch

import pytest

from unit.async_utils import get_defined_tasks, wait_for_completion_async, wait_for_phase_async


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


@pytest.mark.parametrize("completed", [False, True])
async def test_wait_for_phase_accepts_reached_checkpoint_async(*, completed: bool) -> None:
    event = asyncio.Event()
    event.set()
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    if completed:
        future.set_result(42)
    try:
        await wait_for_phase_async(event=event, task=future)
        assert future.done() is completed
    finally:
        future.cancel()


@pytest.mark.parametrize("phase_reached", [False, True])
async def test_wait_for_phase_preserves_early_exception_async(*, phase_reached: bool) -> None:
    event = asyncio.Event()
    if phase_reached:
        event.set()
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    failure = LookupError("operation failed")
    future.set_exception(failure)

    with pytest.raises(LookupError, match="operation failed") as raised:
        await wait_for_phase_async(event=event, task=future)

    assert raised.value is failure


@pytest.mark.parametrize("phase_reached", [False, True])
async def test_wait_for_phase_preserves_operation_cancellation_async(*, phase_reached: bool) -> None:
    event = asyncio.Event()
    if phase_reached:
        event.set()
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    future.cancel("requested cancellation")

    with pytest.raises(asyncio.CancelledError, match="requested cancellation"):
        await wait_for_phase_async(event=event, task=future)


async def test_wait_for_phase_rejects_completion_before_checkpoint_async() -> None:
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    future.set_result(42)

    with pytest.raises(AssertionError, match="completed before the expected test phase"):
        await wait_for_phase_async(event=asyncio.Event(), task=future)


async def test_wait_for_phase_timeout_leaves_operation_owned_by_caller_async() -> None:
    release = asyncio.Event()
    task = asyncio.create_task(release.wait())
    owned_tasks = asyncio.all_tasks()
    try:
        with pytest.raises(TimeoutError, match="watchdog expired"):
            await wait_for_phase_async(event=asyncio.Event(), task=task, timeout=0)
        assert asyncio.all_tasks() == owned_tasks
        assert not task.done()
        assert task.cancelling() == 0

        release.set()
        assert await wait_for_completion_async(future=task)
    finally:
        release.set()
        await task


async def test_wait_for_phase_caller_cancellation_drains_only_observer_async() -> None:
    event, observing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    operation = asyncio.create_task(release.wait())
    observer: asyncio.Task[bool] | None = None
    event_wait = event.wait

    async def observed_wait_async() -> bool:
        nonlocal observer
        observer = asyncio.current_task()
        observing.set()
        return await event_wait()

    with patch.object(event, "wait", side_effect=observed_wait_async):
        phase = asyncio.create_task(wait_for_phase_async(event=event, task=operation))
        try:
            ready = asyncio.create_task(observing.wait())
            await wait_for_completion_async(future=ready)
            phase.cancel("stop observing")
            with pytest.raises(asyncio.CancelledError, match="stop observing"):
                await wait_for_completion_async(future=phase)
            assert observer is not None and observer.done()
            assert not operation.done()
            assert operation.cancelling() == 0
        finally:
            release.set()
            for task in (operation, phase, ready):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, phase, ready, return_exceptions=True)
