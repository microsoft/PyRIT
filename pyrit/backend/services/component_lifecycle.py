# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Cleanup of objects owned by one backend operation."""

import asyncio
import logging
from collections.abc import Callable
from typing import ParamSpec, Protocol, TypeVar, runtime_checkable

_Params = ParamSpec("_Params")
_Component = TypeVar("_Component")
logger = logging.getLogger(__name__)


@runtime_checkable
class TemporaryResource(Protocol):
    """A component with operation-owned resources."""

    async def cleanup_target_async(self) -> None:
        """Release owned resources, not registered dependencies."""
        ...


async def release_component_async(component: object) -> None:
    """Release only a temporary object's explicitly owned resources."""
    if isinstance(component, TemporaryResource):
        task = asyncio.create_task(component.cleanup_target_async())
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError


async def construct_component_async(
    factory: Callable[_Params, _Component], *args: _Params.args, **kwargs: _Params.kwargs
) -> _Component:
    """
    Finish thread construction before cancellation releases ownership.

    Returns:
        _Component: The constructed component, owned by the caller.

    Raises:
        asyncio.CancelledError: After the cancelled operation releases its component.
    """
    task = asyncio.create_task(asyncio.to_thread(factory, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                logger.exception("Component construction failed while the request was cancelled")
                break
        if not task.cancelled() and task.exception() is None:
            await release_component_async(task.result())
        raise
