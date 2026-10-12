# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend compatibility fixtures independent of a packaged workspace stamp."""

from collections.abc import AsyncGenerator, Iterator
from functools import partial
from unittest.mock import patch

import pytest

from pyrit import _compatibility
from pyrit.analytics import AttackResultAnalytics
from pyrit.analytics._execution import AnalyticsExecution
from pyrit.backend.main import app
from pyrit.backend.services.analytics_service import close_analytics_service_async, get_analytics_service
from pyrit.backend.services.attack_service import get_attack_service
from pyrit.backend.services.manual_send_scheduler import get_manual_send_scheduler
from pyrit.backend.services.message_send_service import get_message_send_service
from pyrit.memory import SQLiteMemory


@pytest.fixture
async def analytics_service(sqlite_instance: SQLiteMemory) -> AsyncGenerator[AttackResultAnalytics, None]:
    """Use real isolated memory with finite correctness budgets, not production latency assertions."""
    execution = partial(AnalyticsExecution, queue_timeout=30.0, report_timeout=30.0, quick_timeout=30.0)
    assert get_analytics_service.cache_info().currsize == 0
    with patch("pyrit.analytics.attack_result_analytics.AnalyticsExecution", side_effect=execution):
        try:
            yield get_analytics_service()
        finally:
            await close_analytics_service_async()


@pytest.fixture(autouse=True)
def isolated_manual_message_services() -> Iterator[None]:
    """Keep cached service owners bound to this test's memory and event loop."""
    for factory in (get_attack_service, get_message_send_service, get_manual_send_scheduler):
        factory.cache_clear()
    yield
    for factory in (get_attack_service, get_message_send_service, get_manual_send_scheduler):
        factory.cache_clear()


@pytest.fixture(autouse=True)
def compatibility_id() -> Iterator[str]:
    """Supply startup provenance and initialize clients that deliberately skip lifespan."""
    identity = "0.14.0+g" + "a" * 40
    with (
        patch.object(_compatibility, "get_compatibility_id", return_value=identity),
        patch.object(app.state, "compatibility_id", identity, create=True),
    ):
        yield identity


@pytest.fixture
def compatibility_headers(compatibility_id: str) -> dict[str, str]:
    """Provide the marker required by business API requests."""
    return {_compatibility.COMPATIBILITY_HEADER: compatibility_id}
