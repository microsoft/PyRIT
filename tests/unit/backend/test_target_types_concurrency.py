# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Concurrency regressions for target type routes."""

import asyncio
from threading import Event
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from pyrit.backend.main import app
from pyrit.backend.services.target_service import TargetService


async def test_health_remains_schedulable_during_cold_target_types(compatibility_headers: dict[str, str]) -> None:
    discovery_started = Event()
    discovery_release = Event()
    discovery_finished = Event()
    service = TargetService()

    def _blocking_metadata_discovery() -> list[object]:
        discovery_started.set()
        discovery_release.wait(timeout=30)
        discovery_finished.set()
        return []

    transport = ASGITransport(app=app)
    with (
        patch.object(service._registry, "get_all_registered_class_metadata", side_effect=_blocking_metadata_discovery),
        patch("pyrit.backend.routes.targets.get_target_service", return_value=service),
    ):
        async with AsyncClient(transport=transport, base_url="http://test", headers=compatibility_headers) as client:
            types_request = asyncio.create_task(client.get("/api/targets/types"))
            try:
                try:
                    assert await asyncio.to_thread(discovery_started.wait, 10)
                    health_response = await asyncio.wait_for(client.get("/api/health"), timeout=10)
                    assert health_response.status_code == 200
                    assert not discovery_finished.is_set()
                finally:
                    discovery_release.set()
            except BaseException:
                # Drain the request without replacing the original test failure.
                types_request.cancel()
                await asyncio.gather(types_request, return_exceptions=True)
                raise
            types_response = await asyncio.wait_for(types_request, timeout=10)

    assert types_response.status_code == 200
