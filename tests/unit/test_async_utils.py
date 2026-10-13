# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from typing import assert_type

import pytest

from unit.async_utils import get_defined_tasks, wait_for_completion_async


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
