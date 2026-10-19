# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from typing import TypeVar

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
