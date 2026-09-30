# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Admission, fairness, and cleanup contracts for ordinary manual messages."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from unittest.mock import MagicMock

import pytest

from pyrit.backend.services.manual_send_scheduler import (
    ManualSendConflictError,
    ManualSendQueueFullError,
    ManualSendScheduler,
    get_manual_send_scheduler,
)
from pyrit.converter import Converter

pytestmark = pytest.mark.timeout(10)

ResourceGuard = tuple[Callable[[str], AbstractAsyncContextManager[None]], set[str] | set[int]]


@pytest.fixture(params=["metadata", "converter"])
def resource_guard(request: pytest.FixtureRequest) -> ResourceGuard:
    scheduler = ManualSendScheduler()
    if request.param == "metadata":
        return (
            lambda name: scheduler.metadata_update_async(attack_result_id=name),
            scheduler._metadata_updates,
        )
    converters = {name: MagicMock(spec=Converter) for name in ["first", "second"]}
    return lambda name: scheduler.conversion_async(converters[name]), scheduler._converters


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
            async with scheduler.operation_async():
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


async def test_waiting_operations_enter_in_fifo_order_async() -> None:
    scheduler = ManualSendScheduler(max_concurrency=1, max_operations=3)
    order: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first_async() -> None:
        async with scheduler.operation_async():
            order.append("first")
            first_started.set()
            await release_first.wait()

    async def second_async() -> None:
        async with scheduler.operation_async():
            assert scheduler._active == 1
            order.append("second")
            await asyncio.sleep(0)

    async def last_async() -> None:
        async with scheduler.operation_async():
            order.append("last")

    first = asyncio.create_task(first_async())
    await first_started.wait()
    second = asyncio.create_task(second_async())
    await asyncio.sleep(0)
    last = asyncio.create_task(last_async())
    await asyncio.sleep(0)
    try:
        assert order == ["first"]
    finally:
        release_first.set()
        await asyncio.gather(first, second, last)
    assert order == ["first", "second", "last"]
    assert scheduler._active == 0
    assert not scheduler._queue


@pytest.mark.parametrize("queued", [False, True])
async def test_cancellation_releases_tickets_slots_and_ownership_async(queued: bool) -> None:
    scheduler = ManualSendScheduler(max_concurrency=1, max_operations=2)
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation_async() -> None:
        with scheduler.reserve(conversation_id="cancelled"):
            async with scheduler.operation_async():
                started.set()
                await release.wait()

    if queued:
        async with scheduler.operation_async():
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
    with scheduler.reserve(conversation_id="cancelled"):
        async with scheduler.operation_async():
            assert scheduler._active == 1


async def test_execution_failure_releases_capacity_async() -> None:
    scheduler = ManualSendScheduler(max_concurrency=1, max_operations=1)
    with pytest.raises(RuntimeError, match="provider"):
        with scheduler.reserve(conversation_id="conversation"):
            async with scheduler.operation_async():
                raise RuntimeError("provider")
    with scheduler.reserve(conversation_id="conversation"):
        async with scheduler.operation_async():
            assert scheduler._active == 1
    assert scheduler._active == 0


def test_default_scheduler_is_shared() -> None:
    get_manual_send_scheduler.cache_clear()
    try:
        assert get_manual_send_scheduler() is get_manual_send_scheduler()
    finally:
        get_manual_send_scheduler.cache_clear()


async def test_guards_serialize_only_the_same_resource_async(resource_guard: ResourceGuard) -> None:
    guard, active = resource_guard
    attempted, entered = asyncio.Event(), asyncio.Event()

    async def update_async() -> None:
        attempted.set()
        async with guard("first"):
            entered.set()

    try:
        async with guard("first"):
            waiting = asyncio.create_task(update_async())
            await attempted.wait()
            assert not entered.is_set()
            async with guard("second"):
                assert len(active) == 2
            assert len(active) == 1
    finally:
        await waiting
    assert entered.is_set()
    assert not active


@pytest.mark.parametrize("queued", [False, True])
async def test_cancellation_releases_only_its_own_resource_guard_async(
    *, resource_guard: ResourceGuard, queued: bool
) -> None:
    guard, active = resource_guard
    attempted, entered = asyncio.Event(), asyncio.Event()

    async def update_async() -> None:
        attempted.set()
        async with guard("first"):
            entered.set()
            await asyncio.Event().wait()

    if queued:
        async with guard("first"):
            update = asyncio.create_task(update_async())
            await attempted.wait()
            assert not entered.is_set()
            update.cancel()
            with pytest.raises(asyncio.CancelledError):
                await update
            assert len(active) == 1
    else:
        update = asyncio.create_task(update_async())
        await entered.wait()
        update.cancel()
        with pytest.raises(asyncio.CancelledError):
            await update

    assert not active
    async with guard("first"):
        assert len(active) == 1


async def test_failure_releases_the_resource_guard_async(resource_guard: ResourceGuard) -> None:
    guard, active = resource_guard
    with pytest.raises(RuntimeError, match="failed"):
        async with guard("first"):
            raise RuntimeError("failed")
    assert not active
    async with guard("first"):
        assert len(active) == 1
