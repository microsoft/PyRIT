# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend ownership of the direct SDK dependency, not a forwarding service."""

from functools import lru_cache

from pyrit.analytics import AttackResultAnalytics


@lru_cache(maxsize=1)
def get_analytics_service() -> AttackResultAnalytics:
    """
    Resolve one long-lived SDK facade after startup has configured CentralMemory.

    Returns:
        AttackResultAnalytics: The direct SDK dependency for this runtime.
    """
    return AttackResultAnalytics()


async def get_analytics_service_async() -> AttackResultAnalytics:
    """
    Resolve the cached SDK on the runtime loop, avoiding concurrent threadpool cache misses.

    Returns:
        AttackResultAnalytics: The same SDK owner for concurrent HTTP dependencies.
    """
    return get_analytics_service()


async def close_analytics_service_async() -> None:
    """Drain on the owning loop before releasing the cached memory reference."""
    if get_analytics_service.cache_info().currsize:
        analytics = get_analytics_service()
        try:
            await analytics.close_async()
        finally:
            # Cancellation may propagate after draining; a failed drain must retain its owner.
            if analytics.is_closed:
                get_analytics_service.cache_clear()
