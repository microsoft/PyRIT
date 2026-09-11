# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Concurrency regressions for target catalog routes."""

import asyncio
from threading import Event
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from pyrit.backend.main import app
from pyrit.backend.services.target_service import TargetService


async def test_health_remains_schedulable_during_cold_target_catalog() -> None:
    discovery_started = Event()
    discovery_release = Event()
    discovery_finished = Event()
    service = TargetService()

    def _blocking_metadata_discovery() -> list[object]:
        discovery_started.set()
        discovery_release.wait(timeout=5)
        discovery_finished.set()
        return []

    transport = ASGITransport(app=app)
    with (
        patch.object(service._registry, "get_all_registered_class_metadata", side_effect=_blocking_metadata_discovery),
        patch("pyrit.backend.routes.targets.get_target_service", return_value=service),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            catalog_request = asyncio.create_task(client.get("/api/targets/catalog"))
            assert await asyncio.to_thread(discovery_started.wait, 5)

            try:
                health_response = await client.get("/api/health")
                assert health_response.status_code == 200
                assert not discovery_finished.is_set()
            finally:
                discovery_release.set()
            catalog_response = await catalog_request

    assert catalog_response.status_code == 200
