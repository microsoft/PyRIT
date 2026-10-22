# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Concurrency regressions for target type routes."""

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from pyrit.backend.main import app
from pyrit.backend.services.target_service import TargetService
from unit.async_utils import TEST_WATCHDOG_SECONDS, get_defined_tasks, wait_for_completion_async


@pytest.fixture(params=[1, 2], ids=["one-worker", "two-workers"])
def discovery_executor(request: pytest.FixtureRequest) -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(max_workers=request.param) as executor:
        yield executor


@pytest.mark.parametrize(
    "interruption",
    [None, AssertionError, asyncio.CancelledError],
    ids=["completion", "assertion-failure", "cancellation"],
)
async def test_health_remains_schedulable_during_cold_target_types(
    *,
    compatibility_headers: dict[str, str],
    discovery_executor: ThreadPoolExecutor,
    interruption: type[BaseException] | None,
) -> None:
    loop = asyncio.get_running_loop()
    loop.set_default_executor(discovery_executor)
    discovery_started = asyncio.Event()
    discovery_release = Event()
    discovery_finished = Event()
    service = TargetService()

    def _blocking_metadata_discovery() -> list[object]:
        loop.call_soon_threadsafe(discovery_started.set)
        try:
            assert discovery_release.wait(timeout=TEST_WATCHDOG_SECONDS), "Discovery was not released by the test"
            return []
        finally:
            discovery_finished.set()

    transport = ASGITransport(app=app)
    with (
        patch.object(
            service._registry, "get_all_registered_class_metadata", side_effect=_blocking_metadata_discovery
        ) as discover,
        patch("pyrit.backend.routes.targets.get_target_service", return_value=service),
    ):
        async with AsyncClient(transport=transport, base_url="http://test", headers=compatibility_headers) as client:
            discovery_waiter = asyncio.create_task(discovery_started.wait())
            types_request = asyncio.create_task(client.get("/api/targets/types"))
            health_request = None
            outcome = (
                pytest.raises(interruption, match="Interrupted while discovery is held")
                if interruption
                else nullcontext()
            )
            with outcome:
                try:
                    done, _ = await asyncio.wait(
                        {discovery_waiter, types_request},
                        timeout=TEST_WATCHDOG_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if types_request in done:
                        try:
                            types_request.result()
                        except Exception as error:
                            pytest.fail(
                                f"Target types request completed before discovery: {type(error).__name__}: {error}"
                            )
                        pytest.fail("Target types request completed before discovery was held")
                    assert discovery_waiter in done, "Discovery did not start before the test watchdog expired"
                    health_request = asyncio.create_task(client.get("/api/health"))
                    health_response = await wait_for_completion_async(future=health_request, timeout=10)
                    assert health_response.status_code == 200
                    assert not discovery_finished.is_set()
                    assert not types_request.done()
                    if interruption:
                        raise interruption("Interrupted while discovery is held")
                finally:
                    discovery_release.set()
                    discovery_waiter.cancel()
                    for request_task in get_defined_tasks(health_request):
                        if not request_task.done():
                            request_task.cancel()
                    # Do not cancel the types request: its worker must finish before patches and the client exit.
                    await wait_for_completion_async(
                        future=asyncio.gather(
                            *get_defined_tasks(discovery_waiter, types_request, health_request), return_exceptions=True
                        )
                    )
            assert discovery_finished.is_set()
            assert discovery_waiter.done()
            assert health_request is not None and health_request.done()
            types_response = types_request.result()

    discover.assert_called_once()
    assert types_response.status_code == 200
    assert types_response.json() == {"items": []}
