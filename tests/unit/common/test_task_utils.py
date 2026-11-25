# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Behavioral tests for ``gather_with_cleanup_async``.

The helper's contract: ordered results on success; on failure or caller
cancellation, cancel the siblings, wait for every child to finish its
cancellation cleanup (drain), and only then re-raise the one original
exception. Call sites across the codebase rely on these semantics.
"""

import asyncio

import pytest
from unit.async_utils import TEST_WATCHDOG_SECONDS, drain_tasks_async

from pyrit.common.task_utils import gather_with_cleanup_async


async def test_gather_with_cleanup_returns_empty_list_for_no_tasks_async() -> None:
    assert await gather_with_cleanup_async([]) == []


async def test_gather_with_cleanup_orders_results_by_input_not_completion_async() -> None:
    release_first = asyncio.Event()

    async def finishes_last_async() -> str:
        await asyncio.wait_for(release_first.wait(), timeout=TEST_WATCHDOG_SECONDS)
        return "first"

    async def finishes_first_async() -> str:
        release_first.set()
        return "second"

    assert await gather_with_cleanup_async([finishes_last_async(), finishes_first_async()]) == ["first", "second"]


async def test_gather_with_cleanup_failure_cancels_and_drains_the_sibling_async() -> None:
    sibling_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    failure = RuntimeError("original failure")

    async def hanging_sibling_async() -> None:
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Async cleanup: the helper must wait for this to finish before it raises.
            await asyncio.sleep(0)
            cleanup_finished.set()
            raise

    async def failing_async() -> None:
        await asyncio.wait_for(sibling_started.wait(), timeout=TEST_WATCHDOG_SECONDS)
        raise failure

    with pytest.raises(RuntimeError) as exc_info:
        await gather_with_cleanup_async([hanging_sibling_async(), failing_async()])

    assert exc_info.value is failure
    assert cleanup_finished.is_set()


async def test_gather_with_cleanup_sibling_cleanup_error_does_not_replace_the_failure_async() -> None:
    sibling_started = asyncio.Event()
    failure = RuntimeError("original failure")

    async def noisy_sibling_async() -> None:
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise ValueError("cleanup error") from None

    async def failing_async() -> None:
        await asyncio.wait_for(sibling_started.wait(), timeout=TEST_WATCHDOG_SECONDS)
        raise failure

    with pytest.raises(RuntimeError) as exc_info:
        await gather_with_cleanup_async([noisy_sibling_async(), failing_async()])

    assert exc_info.value is failure


async def test_gather_with_cleanup_caller_cancellation_cancels_and_drains_children_async() -> None:
    child_started = asyncio.Event()
    child_cancelled = asyncio.Event()

    async def child_async() -> None:
        child_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            child_cancelled.set()
            raise

    caller = asyncio.create_task(gather_with_cleanup_async([child_async()]))
    await asyncio.wait_for(child_started.wait(), timeout=TEST_WATCHDOG_SECONDS)
    caller.cancel()

    await drain_tasks_async(caller)
    assert caller.cancelled()
    assert child_cancelled.is_set()


async def test_gather_with_cleanup_absorbs_repeated_cancellation_until_the_drain_completes_async() -> None:
    child_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def slow_cleanup_child_async() -> None:
        child_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await asyncio.wait_for(release_cleanup.wait(), timeout=TEST_WATCHDOG_SECONDS)
            cleanup_finished.set()
            raise

    caller = asyncio.create_task(gather_with_cleanup_async([slow_cleanup_child_async()]))
    await asyncio.wait_for(child_started.wait(), timeout=TEST_WATCHDOG_SECONDS)
    caller.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=TEST_WATCHDOG_SECONDS)

    # A second cancellation mid-drain must not abandon the cleanup.
    caller.cancel()
    await asyncio.sleep(0)
    assert not caller.done()

    release_cleanup.set()
    await drain_tasks_async(caller)
    assert caller.cancelled()
    assert cleanup_finished.is_set()
