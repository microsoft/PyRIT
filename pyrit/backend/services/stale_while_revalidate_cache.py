# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Reusable asynchronous stale-while-revalidate caching."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Generic, TypeVar

_ValueT = TypeVar("_ValueT")

logger = logging.getLogger(__name__)


class StaleWhileRevalidateCache(Generic[_ValueT]):
    """Cache keyed values while refreshing expired entries in the background."""

    def __init__(self, *, ttl_seconds: float, load_async: Callable[[str], Awaitable[_ValueT]]) -> None:
        """Initialize the cache."""
        self._ttl_seconds = ttl_seconds
        self._load_value_async = load_async
        self._entries: dict[str, tuple[float, _ValueT]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._generations: dict[str, int] = {}

    async def get_async(self, *, key: str) -> _ValueT:
        """Return a cached value, loading misses and refreshing expired entries in the background."""
        entry = self._entries.get(key)
        if entry is None:
            return await self._load_async(key=key, force=False)

        if time.monotonic() >= entry[0] and key not in self._refresh_tasks:
            self._refresh_tasks[key] = asyncio.create_task(self._refresh_in_background_async(key=key))
        return deepcopy(entry[1])

    async def refresh_async(self, *, key: str) -> _ValueT:
        """
        Load and cache a fresh value.

        Returns:
            _ValueT: A copy of the refreshed value.
        """
        return await self._load_async(key=key, force=True)

    async def update_async(self, *, key: str, update_async: Callable[[], Awaitable[_ValueT]]) -> _ValueT:
        """
        Persist and cache a value while excluding loads for the same key.

        Returns:
            _ValueT: A copy of the persisted value.
        """
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            value = await update_async()
            self.set(key=key, value=value)
            return deepcopy(value)

    def set(self, *, key: str, value: _ValueT) -> None:
        """Store a value and supersede any in-progress load."""
        self._advance_generation(key)
        self._entries[key] = (time.monotonic() + self._ttl_seconds, value)

    def invalidate(self, key: str) -> None:
        """Remove a value and prevent an in-progress load from restoring it."""
        self._advance_generation(key)
        self._entries.pop(key, None)

    def get_refresh_task(self, key: str) -> asyncio.Task[None] | None:
        """Return the active background refresh task for a key, if any."""
        return self._refresh_tasks.get(key)

    async def _load_async(
        self,
        *,
        key: str,
        force: bool,
    ) -> _ValueT:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._entries.get(key)
            if not force and entry is not None:
                return deepcopy(entry[1])

            generation = self._generations.get(key, 0)
            value = await self._load_value_async(key)
            if self._generations.get(key, 0) == generation:
                self._entries[key] = (time.monotonic() + self._ttl_seconds, value)
            return deepcopy(value)

    async def _refresh_in_background_async(
        self,
        *,
        key: str,
    ) -> None:
        task = asyncio.current_task()
        try:
            await self._load_async(key=key, force=True)
        except Exception:
            logger.warning("Failed to refresh cache entry %r", key, exc_info=True)
        finally:
            if self._refresh_tasks.get(key) is task:
                self._refresh_tasks.pop(key, None)

    def _advance_generation(self, key: str) -> None:
        self._generations[key] = self._generations.get(key, 0) + 1
