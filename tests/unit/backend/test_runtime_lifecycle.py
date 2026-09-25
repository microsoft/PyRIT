# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Admission and fail-fast runtime replacement invariants."""

import asyncio
import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

import pyrit.backend.services.runtime_lifecycle as lifecycle_module
from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.middleware.runtime import RuntimeAdmissionMiddleware
from pyrit.backend.routes import configuration, health
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
from pyrit.setup.configuration_loader import ConfigurationLoader


@pytest.fixture
def runtime(tmp_path: Path) -> Generator[RuntimeLifecycle, None, None]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "memory_db_type: in_memory\nenable_live_reinitialization: true\n",
        encoding="utf-8",
    )
    source = ConfigurationFileService(config_file_value=str(config_path))
    app = FastAPI()
    with patch.dict(os.environ, {}, clear=True):
        service = RuntimeLifecycle(app=app, source=source)
    assert service.topology_supported
    service.state = "ready"
    service.generation = "original"
    app.state.runtime_lifecycle = service
    app.state.configuration_file_service = source
    app.add_middleware(RuntimeAdmissionMiddleware)
    app.include_router(configuration.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: None
    config = ConfigurationLoader(
        memory_db_type="in_memory",
        env_files=[],
        enable_live_reinitialization=True,
    )
    prepared = MagicMock()
    with (
        patch.object(service, "_load_async", AsyncMock(return_value=config)),
        patch.object(service, "_management_async", AsyncMock()),
        patch.object(config, "preflight_reinitialization_async", AsyncMock(return_value=prepared)),
        patch.object(config, "apply_prepared_reinitialization_async", AsyncMock()),
        patch.object(lifecycle_module, "validate_reinitialization_memory"),
        patch.object(lifecycle_module, "resolve_environment_async", AsyncMock(return_value={"NEW_VALUE": "new"})),
        patch.object(lifecycle_module, "close_services_async", AsyncMock()),
        patch.object(lifecycle_module, "peek_scenario_run_service", return_value=None),
        patch.object(lifecycle_module, "outstanding_estimates", return_value=0),
    ):
        yield service


async def apply_async(runtime: RuntimeLifecycle) -> None:
    _, version = await runtime.source.read_with_version_async()
    result = runtime.begin_apply(version=version)
    assert result["outcome"] == "accepted"
    assert runtime.apply_task is not None
    await runtime.apply_task


async def test_success_preflights_then_replaces_idle_runtime(runtime: RuntimeLifecycle) -> None:
    await apply_async(runtime)
    assert runtime.state == "ready"
    assert runtime.generation != "original"
    config = await runtime._load_async()
    config.preflight_reinitialization_async.assert_awaited_once_with(environment_values={"NEW_VALUE": "new"})
    lifecycle_module.close_services_async.assert_awaited_once()
    config.apply_prepared_reinitialization_async.assert_awaited_once()
    assert runtime.version == (await runtime.source.read_with_version_async())[1]


async def test_saved_opt_in_is_required(runtime: RuntimeLifecycle) -> None:
    config = await runtime._load_async()
    config.enable_live_reinitialization = False
    await apply_async(runtime)
    assert runtime.outcome == "unsupported"
    assert runtime.state == "ready"
    config.preflight_reinitialization_async.assert_not_awaited()
    lifecycle_module.close_services_async.assert_not_awaited()


async def test_stale_version_and_invalid_preflight_do_not_mutate(runtime: RuntimeLifecycle) -> None:
    runtime.begin_apply(version="old")
    assert runtime.apply_task is not None
    await runtime.apply_task
    assert runtime.outcome == "version-conflict"
    with patch.object(runtime, "_load_async", AsyncMock(side_effect=ValueError("secret"))):
        await apply_async(runtime)
    assert runtime.outcome == "invalid-configuration"
    assert runtime.state == "ready"
    assert "secret" not in runtime.message
    lifecycle_module.close_services_async.assert_not_awaited()


async def test_active_work_rejects_apply_without_stopping_or_mutating(runtime: RuntimeLifecycle) -> None:
    service = MagicMock()
    service.active_work.return_value = (["running-id"], 1)
    with patch.object(lifecycle_module, "peek_scenario_run_service", return_value=service):
        await apply_async(runtime)
    assert runtime.outcome == "busy"
    assert runtime.state == "ready"
    lifecycle_module.close_services_async.assert_not_awaited()
    assert not hasattr(service, "request_stop") or not service.request_stop.called


async def test_second_idle_check_closes_admission_race(runtime: RuntimeLifecycle) -> None:
    with patch.object(runtime, "_has_active_work", side_effect=[False, True]):
        await apply_async(runtime)
    assert runtime.outcome == "busy"
    assert runtime.state == "ready"
    lifecycle_module.close_services_async.assert_not_awaited()


async def test_mutation_failure_requires_restart_and_cannot_retry(runtime: RuntimeLifecycle) -> None:
    config = await runtime._load_async()
    config.apply_prepared_reinitialization_async.side_effect = ValueError("credential=secret")
    await apply_async(runtime)
    assert runtime.state == "restart-required"
    assert runtime.outcome == "restart-required"
    assert "secret" not in runtime.message
    _, version = await runtime.source.read_with_version_async()
    assert runtime.begin_apply(version=version)["outcome"] == "restart-required"


async def test_apply_survives_client_and_concurrent_apply_is_busy(runtime: RuntimeLifecycle) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    config = await runtime._load_async()

    async def initialize_async(**kwargs: object) -> None:
        entered.set()
        await release.wait()

    config.apply_prepared_reinitialization_async.side_effect = initialize_async
    _, version = await runtime.source.read_with_version_async()
    runtime.begin_apply(version=version)
    await entered.wait()
    assert runtime.begin_apply(version=version)["outcome"] == "busy"
    assert runtime.state == "initializing"
    release.set()
    assert runtime.apply_task is not None
    await runtime.apply_task
    assert runtime.state == "ready"


async def test_disconnected_send_remains_owned_and_blocks_apply(runtime: RuntimeLifecycle) -> None:
    entered, release, persisted = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @runtime.app.post("/api/attacks/id/messages")
    async def send_async() -> dict[str, bool]:
        entered.set()
        await release.wait()
        persisted.set()
        return {"stored": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        request = asyncio.create_task(client.post("/api/attacks/id/messages"))
        await entered.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert runtime.active_work()["sends"] == 1
        await apply_async(runtime)
        assert runtime.outcome == "busy"
        assert runtime.state == "ready"
        release.set()
        await persisted.wait()
        await asyncio.gather(*runtime.operations)
        await apply_async(runtime)
        assert runtime.state == "ready"


async def test_background_estimates_reject_apply(runtime: RuntimeLifecycle) -> None:
    with patch.object(lifecycle_module, "outstanding_estimates", return_value=2):
        await apply_async(runtime)
    assert runtime.state == "ready"
    assert runtime.outcome == "busy"
    lifecycle_module.close_services_async.assert_not_awaited()


async def test_apply_denies_writes_but_allows_repair_reads(runtime: RuntimeLifecycle) -> None:
    async with runtime.edit_lock:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
            assert (await client.put("/api/config", json={})).status_code == 503
            assert (await client.get("/api/config")).status_code == 200
            assert runtime.begin_apply(version="v")["outcome"] == "busy"


async def test_non_admin_apply_and_status_remain_denied(runtime: RuntimeLifecycle) -> None:
    runtime.app.dependency_overrides.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        assert (await client.get("/api/config/runtime")).status_code == 403
        assert (await client.post("/api/config/runtime/apply", json={"version": "v"})).status_code == 403


@pytest.mark.parametrize(
    "worker_setting",
    ["WEB_CONCURRENCY", "UVICORN_WORKERS", "PYRIT_API_WORKERS", "PYRIT_REPLICAS"],
)
def test_multiple_workers_disable_apply(worker_setting: str) -> None:
    with patch.dict(os.environ, {worker_setting: "2"}, clear=True):
        service = RuntimeLifecycle(app=FastAPI(), source=ConfigurationFileService(config_file_value=None))
    assert not service.topology_supported


async def test_invalid_cold_configuration_requires_restart_but_retains_raw_repair(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("broken: [", encoding="utf-8")
    source = ConfigurationFileService(config_file_value=str(path))
    app = FastAPI()
    service = RuntimeLifecycle(app=app, source=source)
    await service.startup_async()
    assert service.state == "restart-required"
    assert not app.state.allow_custom_initializers
    assert app.state.environment_file_service is None
    content, version = await source.read_with_version_async()
    assert content == "broken: ["
    await source.update_async("memory_db_type: in_memory\n", expected_version=version)
    assert "in_memory" in await source.read_async()


def test_authorization_policy_does_not_change_with_environment(runtime: RuntimeLifecycle) -> None:
    runtime.app.state.auth_environment["PYRIT_ALLOW_UNAUTHENTICATED_ADMIN"] = ""
    request = Request({"type": "http", "app": runtime.app})
    with patch.dict(os.environ, {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "true"}):
        with pytest.raises(Exception) as error:
            require_admin(request)
    assert error.value.status_code == 403
