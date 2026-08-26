# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the asynchronous stale-while-revalidate cache."""

import asyncio
from unittest.mock import patch

from pyrit.backend.services.stale_while_revalidate_cache import StaleWhileRevalidateCache


async def test_invalidation_during_refresh_prevents_stale_repopulation() -> None:
    """Test that an in-flight refresh cannot restore an invalidated value."""
    refresh_started = asyncio.Event()
    allow_refresh = asyncio.Event()
    load_count = 0

    async def load_async(_: str) -> str:
        nonlocal load_count
        load_count += 1
        if load_count == 2:
            refresh_started.set()
            await allow_refresh.wait()
        return f"value-{load_count}"

    cache = StaleWhileRevalidateCache[str](ttl_seconds=10.0, load_async=load_async)
    with patch("pyrit.backend.services.stale_while_revalidate_cache.time.monotonic", return_value=100.0) as clock:
        assert await cache.get_async(key="key") == "value-1"

        clock.return_value = 111.0
        assert await cache.get_async(key="key") == "value-1"
        refresh_task = cache.get_refresh_task("key")
        assert refresh_task is not None
        await refresh_started.wait()

        cache.invalidate("key")
        allow_refresh.set()
        await refresh_task

        assert await cache.get_async(key="key") == "value-3"


async def test_cached_mutable_values_are_copied() -> None:
    """Test that callers cannot mutate the value retained by the cache."""

    async def load_async(_: str) -> list[str]:
        return ["original"]

    cache = StaleWhileRevalidateCache[list[str]](ttl_seconds=10.0, load_async=load_async)

    first = await cache.get_async(key="key")
    first.append("changed")

    assert await cache.get_async(key="key") == ["original"]
