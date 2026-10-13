# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend compatibility fixtures independent of a packaged workspace stamp."""

from collections.abc import AsyncGenerator, Iterator
from functools import partial
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.analytics import AttackResultAnalytics
from pyrit.analytics._execution import AnalyticsExecution
from pyrit.backend.services.analytics_service import close_analytics_service_async, get_analytics_service
from pyrit.backend.services.attack_service import get_attack_service
from pyrit.backend.services.dataset_service import get_dataset_service
from pyrit.backend.services.manual_send_scheduler import get_manual_send_scheduler
from pyrit.backend.services.message_send_service import get_message_send_service
from pyrit.memory import SQLiteMemory
from test_support.fixtures import compatibility_headers, compatibility_id  # noqa: F401


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


@pytest.fixture
def managed_media_path(*, sqlite_instance: SQLiteMemory, patch_central_database: MagicMock) -> Path:
    """Create a file in the isolated memory's media directory."""
    assert sqlite_instance.results_path is not None
    path = Path(sqlite_instance.results_path) / "prompt-memory-entries" / "image.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")
    return path.resolve()


@pytest.fixture(autouse=True)
def isolated_manual_message_services() -> Iterator[None]:
    """Keep cached service owners bound to this test's memory and event loop."""
    for factory in (get_attack_service, get_message_send_service, get_manual_send_scheduler, get_dataset_service):
        factory.cache_clear()
    yield
    for factory in (get_attack_service, get_message_send_service, get_manual_send_scheduler, get_dataset_service):
        factory.cache_clear()
