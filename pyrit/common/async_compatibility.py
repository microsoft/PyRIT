# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from functools import wraps
from typing import Any, Concatenate, ParamSpec, TypeVar, cast

from pyrit.common.deprecation import print_deprecation_message

P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T")


async def run_legacy_sync_async(operation: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """
    Finish a legacy operation off-loop before propagating cancellation.

    Returns:
        R: The operation result.

    Raises:
        asyncio.CancelledError: After the worker finishes when the caller cancels.
    """
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


def legacy_sync_override(
    original: Callable[[], Callable[Concatenate[T, P], R]],
) -> Callable[[Callable[Concatenate[T, P], Awaitable[R]]], Callable[Concatenate[T, P], Coroutine[Any, Any, R]]]:
    """
    Preserve an unmigrated subclass's synchronous override during deprecation.

    The built-in implementation always uses its async method. A subclass that
    overrides both methods also uses its async method, including calls to super.

    Returns:
        Callable: A signature-preserving decorator for an explicit async method.
    """

    def decorate(
        method: Callable[Concatenate[T, P], Awaitable[R]],
    ) -> Callable[Concatenate[T, P], Coroutine[Any, Any, R]]:
        @wraps(method)
        async def wrapper_async(self: T, *args: P.args, **kwargs: P.kwargs) -> R:
            sync_method = original()
            async_name = wrapper_async.__name__
            sync_name = async_name.removesuffix("_async")
            override = getattr(type(self), sync_name, sync_method)
            if getattr(type(self), async_name, None) is wrapper_async and override is not sync_method:
                print_deprecation_message(
                    old_item=f"{type(self).__name__}.{sync_name} override",
                    new_item=f"{type(self).__name__}.{async_name}",
                    removed_in="1.4.0",
                )
                legacy_method = cast("Callable[Concatenate[T, P], R]", override)
                return await run_legacy_sync_async(legacy_method, self, *args, **kwargs)
            return await method(self, *args, **kwargs)

        return wrapper_async

    return decorate
