# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Admission, fairness, and cleanup contracts for ordinary manual messages."""

import asyncio

import pytest

from pyrit.backend.services.manual_send_scheduler import (
    ManualSendConflictError,
    ManualSendQueueFullError,
    ManualSendScheduler,
    get_manual_send_scheduler,
)

pytestmark = pytest.mark.timeout(10)


@pytest.mark.parametrize(("concurrency", "operations"), [(0, 3), (-1, 3), (4, 3), (1, 0)])
def test_invalid_limits(*, concurrency: int, operations: int) -> None:
    with pytest.raises(ValueError, match="1 <= max_concurrency <= max_operations"):
        ManualSendScheduler(max_concurrency=concurrency, max_operations=operations)


def test_bounded_admission_and_conversation_ownership() -> None:
    scheduler = ManualSendScheduler(max_concurrency=1, max_operations=2)
    with scheduler.reserve(conversation_id="first"), scheduler.reserve(conversation_id="second"):
        with pytest.raises(ManualSendConflictError):
            with scheduler.reserve(conversation_id="first"):
                pytest.fail("A conversation cannot be admitted twice")
        with pytest.raises(ManualSendQueueFullError):
            with scheduler.reserve(conversation_id="third"):
                pytest.fail("Admission cannot exceed its bound")
    assert not scheduler._conversations
    with scheduler.reserve(conversation_id="first"):
        assert scheduler._conversations == {"first"}


@pytest.mark.parametrize("concurrency", [1, 3])
async def test_execution_budget_is_shared_async(concurrency: int) -> None:
    scheduler = ManualSendScheduler(max_concurrency=concurrency, max_operations=6)
    full = asyncio.Event()
    release = asyncio.Event()
    active = 0
    peak = 0

    async def execute_async(index: int) -> None:
        nonlocal active, peak
        with scheduler.reserve(conversation_id=str(index)):
            async with scheduler.operation_async(exclusive=False):
                active += 1
                peak = max(peak, active)
                if active == concurrency:
                    full.set()
                await release.wait()
                active -= 1

    tasks = [asyncio.create_task(execute_async(index)) for index in range(6)]
    try:
        await full.wait()
        assert scheduler._active == concurrency
        assert len(scheduler._queue) == 6 - concurrency
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert peak == concurrency
    assert scheduler._active == 0
    assert not scheduler._conversations


async def test_exclusive_operation_does_not_starve_behind_parallel_work_async() -> None:
    scheduler = ManualSendScheduler(max_concurrency=2, max_operations=3)
    order: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first_async() -> None:
        async with scheduler.operation_async(exclusive=False):
            order.append("first")
            first_started.set()
            await release_first.wait()

    async def exclusive_async() -> None:
        async with scheduler.operation_async(exclusive=True):
            assert scheduler._active == 1
            order.append("exclusive")
            await asyncio.sleep(0)

    async def last_async() -> None:
        async with scheduler.operation_async(exclusive=False):
            order.append("last")

    first = asyncio.create_task(first_async())
    await first_started.wait()
    exclusive = asyncio.create_task(exclusive_async())
    await asyncio.sleep(0)
    last = asyncio.create_task(last_async())
    await asyncio.sleep(0)
    try:
        assert order == ["first"]
    finally:
        release_first.set()
        await asyncio.gather(first, exclusive, last)
    assert order == ["first", "exclusive", "last"]
    assert scheduler._active == 0
    assert not scheduler._queue


@pytest.mark.parametrize("exclusive", [False, True])
@pytest.mark.parametrize("queued", [False, True])
async def test_cancellation_releases_tickets_slots_and_ownership_async(*, exclusive: bool, queued: bool) -> None:
    scheduler = ManualSendScheduler(max_concurrency=1, max_operations=2)
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation_async() -> None:
        with scheduler.reserve(conversation_id="cancelled"):
            async with scheduler.operation_async(exclusive=exclusive):
                started.set()
                await release.wait()

    if queued:
        async with scheduler.operation_async(exclusive=False):
            operation = asyncio.create_task(operation_async())
            await asyncio.sleep(0)
            assert not started.is_set()
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
    else:
        operation = asyncio.create_task(operation_async())
        await started.wait()
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation

    assert not scheduler._queue
    assert not scheduler._conversations
    assert scheduler._active == 0
    assert not scheduler._exclusive
    with scheduler.reserve(conversation_id="cancelled"):
        async with scheduler.operation_async(exclusive=True):
            assert scheduler._active == 1


@pytest.mark.parametrize("exclusive", [False, True])
async def test_execution_failure_releases_capacity_async(exclusive: bool) -> None:
    scheduler = ManualSendScheduler(max_concurrency=1, max_operations=1)
    with pytest.raises(RuntimeError, match="provider"):
        with scheduler.reserve(conversation_id="conversation"):
            async with scheduler.operation_async(exclusive=exclusive):
                raise RuntimeError("provider")
    with scheduler.reserve(conversation_id="conversation"):
        async with scheduler.operation_async(exclusive=False):
            assert scheduler._active == 1
    assert scheduler._active == 0
    assert not scheduler._exclusive


def test_default_scheduler_is_shared() -> None:
    get_manual_send_scheduler.cache_clear()
    try:
        assert get_manual_send_scheduler() is get_manual_send_scheduler()
    finally:
        get_manual_send_scheduler.cache_clear()
