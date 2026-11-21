# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend isolation, compatibility, and request-loop resource ownership."""

from collections.abc import AsyncGenerator, Generator, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from anyio import from_thread
from anyio.from_thread import BlockingPortal, start_blocking_portal

from pyrit.analytics import AttackResultAnalytics
from pyrit.analytics._execution import AnalyticsExecution
from pyrit.backend.services import get_attack_service, get_converter_service, get_dataset_service, get_target_service
from pyrit.backend.services.analytics_service import close_analytics_service_async, get_analytics_service
from pyrit.backend.services.manual_send_scheduler import get_manual_send_scheduler
from pyrit.backend.services.message_send_service import get_message_send_service
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.backend.services.technique_service import get_technique_service
from pyrit.memory import SQLiteMemory
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage
from test_support.fixtures import authentication_policy, compatibility_headers, compatibility_id  # noqa: F401
from unit.async_utils import TEST_WATCHDOG_SECONDS


@pytest.fixture(autouse=True)
def close_test_client_memory_before_loop_shutdown(request: pytest.FixtureRequest) -> Iterator[None]:
    """Dispose fixture-owned engines before TestClient's AnyIO portal closes its loop."""
    if "sqlite_instance" not in request.fixturenames:
        yield
        return

    @contextmanager
    def memory_portal(*args: Any, **kwargs: Any) -> Generator[BlockingPortal, None, None]:
        # Preserve normal fixture ordering so memory tears down before fixture-owned executors.
        memory: SQLiteMemory = request.getfixturevalue("sqlite_instance")
        with start_blocking_portal(*args, **kwargs) as portal:
            try:
                yield portal
            finally:
                portal.call(memory.dispose_loop_resources_async)

    with patch.object(from_thread, "start_blocking_portal", memory_portal):
        yield


@pytest.fixture
async def analytics_service(sqlite_instance: SQLiteMemory) -> AsyncGenerator[AttackResultAnalytics, None]:
    """Use real isolated memory with finite correctness budgets, not production latency assertions."""
    execution = partial(
        AnalyticsExecution,
        queue_timeout=TEST_WATCHDOG_SECONDS,
        report_timeout=TEST_WATCHDOG_SECONDS,
        quick_timeout=TEST_WATCHDOG_SECONDS,
    )
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


_ISOLATED_SERVICE_FACTORIES = (
    get_attack_service,
    get_message_send_service,
    get_manual_send_scheduler,
    get_dataset_service,
    get_target_service,
    get_converter_service,
    get_scorer_service,
    get_technique_service,
)


@pytest.fixture(autouse=True)
def isolated_manual_message_services() -> Iterator[None]:
    """Keep cached service owners bound to this test's memory, registries, and event loop."""
    for factory in _ISOLATED_SERVICE_FACTORIES:
        factory.cache_clear()
    yield
    for factory in _ISOLATED_SERVICE_FACTORIES:
        factory.cache_clear()


@pytest.fixture(autouse=True)
def isolated_saved_registry_entries(tmp_path: Path) -> Iterator[None]:
    """Keep backend startup and apply from restoring entries saved on the machine running the tests."""
    with patch.object(RegistryRecipeStorage, "_get_default_storage_dir", return_value=tmp_path / "registry_recipes"):
        yield
