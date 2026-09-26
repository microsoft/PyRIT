# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from collections.abc import Awaitable, Iterable
from typing import TypeVar

TaskResultT = TypeVar("TaskResultT")


async def gather_with_cleanup_async(tasks: Iterable[Awaitable[TaskResultT]]) -> list[TaskResultT]:
    """
    Gather ordered results, cancelling and draining siblings on failure or cancellation.

    Caller cancellation is handled here rather than forwarded by the initial gather.
    Children already processing cancellation are only drained. Further caller cancellation
    is delivered after cleanup, and cleanup errors must not replace the original failure.

    Returns:
        list[TaskResultT]: Results in input order.
    """
    scheduled_tasks: list[asyncio.Future[TaskResultT]] = [asyncio.ensure_future(task) for task in tasks]
    group = asyncio.gather(*scheduled_tasks)
    try:
        return await asyncio.shield(group)
    except BaseException:
        for task in scheduled_tasks:
            if not task.done() and (not isinstance(task, asyncio.Task) or not task.cancelling()):
                task.cancel()
        drain = asyncio.gather(*scheduled_tasks, return_exceptions=True)
        outer_cancellation: asyncio.CancelledError | None = None
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError as cancellation:
                outer_cancellation = cancellation
        drain.result()
        # Its shield may have been cancelled before the original gather observed a child error.
        group.exception()
        if outer_cancellation:
            raise outer_cancellation from None
        raise
