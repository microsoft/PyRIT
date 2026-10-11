# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from typing import TypeVar

T = TypeVar("T")


def get_defined_tasks(*tasks: asyncio.Task[T] | None) -> list[asyncio.Task[T]]:
    """Retain owned tasks even when a test fails before creating all of them."""
    return [task for task in tasks if task is not None]


async def wait_for_completion_async(*, future: asyncio.Future[T], timeout: float = 30) -> T:
    """Bound a test wait without injecting cancellation into the operation under test."""
    done, _ = await asyncio.wait({future}, timeout=timeout)
    if not done:
        raise TimeoutError("The operation under test did not complete before the test watchdog expired.")
    return future.result()
