# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import math
import sys
from collections.abc import Sequence
from contextlib import suppress
from typing import Any, TypeVar

T = TypeVar("T")

# Deadlock backstop for correctness tests, not a latency budget.
TEST_WATCHDOG_SECONDS = 300.0


def get_defined_tasks(*tasks: asyncio.Task[T] | None) -> list[asyncio.Task[T]]:
    """Retain owned tasks even when a test fails before creating all of them."""
    return [task for task in tasks if task is not None]


async def wait_for_completion_async(*, future: asyncio.Future[T], timeout: float = TEST_WATCHDOG_SECONDS) -> T:
    """Bound a test wait without injecting cancellation into the operation under test.

    The default is a deadlock backstop rather than a latency assertion, so it stays finite to keep a
    hung test from hanging forever while remaining unreachable on a loaded CI runner. Callers that
    assert a wait has already finished pass ``timeout=0`` explicitly.
    """
    done, _ = await asyncio.wait({future}, timeout=timeout)
    if not done:
        raise TimeoutError("The operation under test did not complete before the test watchdog expired.")
    return future.result()


async def drain_tasks_async(*tasks: asyncio.Task[T], timeout: float = TEST_WATCHDOG_SECONDS) -> list[T | BaseException]:
    """Drain released workers' owning tasks before propagating cancellation.

    Callers must release worker gates first and retain tasks that still own their workers.
    A watchdog expiry reports incomplete cleanup without cancelling those tasks.
    Unlike ``settle_tasks_async``, cancellation must not detach a ``to_thread`` worker
    from its owning task: cancelling that task cannot stop the underlying thread.
    """
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("The cleanup watchdog must be finite and non-negative.")
    completion = asyncio.gather(*tasks, return_exceptions=True)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    active_exception = sys.exception()
    cancellation = active_exception if isinstance(active_exception, asyncio.CancelledError) else None
    while not completion.done():
        try:
            done, _ = await asyncio.wait({completion}, timeout=max(0, deadline - loop.time()))
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
            continue
        if not done:
            raise TimeoutError("Owned tasks did not finish before the cleanup watchdog expired.")
    results = completion.result()
    if cancellation is not None:
        raise cancellation
    return results


async def settle_tasks_async(*tasks: asyncio.Task[Any], timeout: float = TEST_WATCHDOG_SECONDS) -> None:
    """Join owned tasks, cancelling and draining stragglers before reporting watchdog expiry.

    Release test-controlled gates first. Unlike an observation wait, teardown must retain
    ownership until cancellation cleanup finishes, even after the watchdog has expired.
    """
    joined = asyncio.gather(*tasks, return_exceptions=True)
    try:
        await wait_for_completion_async(future=joined, timeout=timeout)
    except TimeoutError:
        joined.cancel()
        with suppress(asyncio.CancelledError):
            await joined
        raise


async def wait_for_event_async(
    *,
    event: asyncio.Event,
    tasks: Sequence[asyncio.Future[Any]],
    timeout: float = TEST_WATCHDOG_SECONDS,
) -> None:
    """Report task completion or failure only when readiness has not been signalled.

    Once readiness is set, the caller owns observing the operation's outcome, including
    failures that complete before this readiness observer resumes.
    """
    ready = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({ready, *tasks}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if event.is_set():
            return
        if not done:
            raise TimeoutError("The operation did not signal readiness before the test watchdog expired.")
        for task in tasks:
            if task.done():
                task.result()
        raise AssertionError("The operation completed without signalling readiness.")
    finally:
        ready.cancel()
        with suppress(asyncio.CancelledError):
            await ready


async def wait_for_worker_ready_async(
    *, started: asyncio.Event, operation: asyncio.Future[T], timeout: float = TEST_WATCHDOG_SECONDS
) -> None:
    """Observe readiness while requiring the worker to remain held for concurrent assertions.

    Workers signal with ``loop.call_soon_threadsafe(started.set)``. The caller still owns releasing
    and joining the operation, including when this wait fails or is cancelled.
    """
    await wait_for_event_async(event=started, tasks=[operation], timeout=timeout)
    if operation.done():
        operation.result()
        raise AssertionError("The operation completed before the test observed worker readiness.")
