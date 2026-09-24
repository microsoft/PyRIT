# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Admission, recovery and stop/drain invariants."""

import asyncio
import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

import pyrit.backend.services.runtime_lifecycle as lifecycle_module
from pyrit.backend.middleware.auth import AuthenticatedUser, require_admin
from pyrit.backend.middleware.runtime import RuntimeAdmissionMiddleware
from pyrit.backend.routes import configuration, health
from pyrit.backend.routes import initializers as initializer_routes
from pyrit.backend.routes import scenarios as scenario_routes
from pyrit.backend.routes import targets as target_routes
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
from pyrit.setup.configuration_loader import ConfigurationLoader
from pyrit.setup.initialization import reset_setup_registries


@pytest.fixture
def runtime(tmp_path: Path) -> Generator[RuntimeLifecycle, None, None]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("memory_db_type: in_memory\n", encoding="utf-8")
    source = ConfigurationFileService(config_file_value=str(config_path))
    app = FastAPI()
    with patch.dict(os.environ, {}, clear=True):
        service = RuntimeLifecycle(app=app, source=source)
    assert service.enabled
    service.state = "ready"
    service.generation = "original"
    app.state.runtime_lifecycle = service
    app.state.configuration_file_service = source
    app.add_middleware(RuntimeAdmissionMiddleware)
    app.include_router(configuration.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: None
    config = ConfigurationLoader(memory_db_type="in_memory", env_files=[])
    with (
        patch.object(service, "_load_async", AsyncMock(return_value=config)),
        patch.object(service, "_management_async", AsyncMock()),
        patch.object(config, "initialize_pyrit_async", AsyncMock()),
        patch.object(lifecycle_module, "validate_reinitialization_memory"),
        patch.object(lifecycle_module, "resolve_environment_async", AsyncMock(return_value={"NEW_VALUE": "new"})),
        patch.object(lifecycle_module, "close_services_async", AsyncMock()),
        patch.object(lifecycle_module, "peek_scenario_run_service", return_value=None),
        patch.object(lifecycle_module, "outstanding_estimates", return_value=0),
    ):
        yield service


async def apply_async(runtime: RuntimeLifecycle, *, stop: bool = False, revision: int | None = None) -> None:
    _, version = await runtime.source.read_with_version_async()
    result = runtime.begin_apply(version=version, stop_scenarios=stop, work_revision=revision)
    assert result["outcome"] == "accepted"
    assert runtime.apply_task is not None
    await runtime.apply_task


async def test_success_reloads_configuration_and_advances_generation(runtime: RuntimeLifecycle) -> None:
    await apply_async(runtime)
    assert runtime.state == "ready"
    assert runtime.generation != "original"
    config = await runtime._load_async()
    config.initialize_pyrit_async.assert_awaited_once()
    assert config.initialize_pyrit_async.call_args.kwargs["reinitialize"] is True
    assert runtime.version == (await runtime.source.read_with_version_async())[1]


async def test_stale_version_and_invalid_preflight_do_not_reset(runtime: RuntimeLifecycle) -> None:
    runtime.begin_apply(version="old", stop_scenarios=True, work_revision=0)
    await runtime.apply_task
    assert runtime.outcome == "version-conflict"
    assert runtime.state == "ready"
    with patch.object(runtime, "_load_async", AsyncMock(side_effect=ValueError("secret"))):
        await apply_async(runtime)
    assert runtime.outcome == "invalid-configuration"
    assert runtime.state == "ready"
    assert "secret" not in runtime.message
    lifecycle_module.close_services_async.assert_not_awaited()


async def test_confirmation_is_required_and_new_work_invalidates_warning(runtime: RuntimeLifecycle) -> None:
    service = MagicMock()
    service.active_work.return_value = (["running-id"], 1)
    with patch.object(lifecycle_module, "peek_scenario_run_service", return_value=service):
        await apply_async(runtime)
        assert runtime.outcome == "confirmation-required"
        await apply_async(runtime, stop=True, revision=-1)
        assert runtime.outcome == "confirmation-required"
        service.request_stop.assert_not_called()


async def test_timeout_is_nonmutating_retry_after_drain_succeeds(runtime: RuntimeLifecycle) -> None:
    service = MagicMock()
    service.active_work.return_value = (["running-id"], 1)
    runtime.DRAIN_TIMEOUT_SECONDS = 0
    with (
        patch.object(lifecycle_module, "peek_scenario_run_service", return_value=service),
        patch.dict(os.environ, {"NEW_VALUE": "old"}),
    ):
        await apply_async(runtime, stop=True, revision=0)
        assert runtime.state == "blocked"
        assert runtime.outcome == "stop-timeout"
        assert os.environ["NEW_VALUE"] == "old"
        assert runtime.generation == "original"
        lifecycle_module.close_services_async.assert_not_awaited()
        config = await runtime._load_async()
        config.initialize_pyrit_async.assert_not_awaited()
        service.active_work.return_value = ([], 0)
        await apply_async(runtime)
        assert runtime.state == "ready"
        service.reopen.assert_not_called()


async def test_cancel_pending_reopens_without_restarting_runs(runtime: RuntimeLifecycle) -> None:
    runtime.state, runtime._previous_state = "blocked", "ready"
    service = MagicMock()
    service.active_work.return_value = ([], 0)
    with patch.object(lifecycle_module, "peek_scenario_run_service", return_value=service):
        assert runtime.cancel_pending()["state"] == "ready"
    service.reopen.assert_called_once()
    lifecycle_module.close_services_async.assert_not_awaited()


async def test_execution_failure_blocks_runtime_and_retry_recovers(runtime: RuntimeLifecycle) -> None:
    config = await runtime._load_async()
    config.initialize_pyrit_async.side_effect = [ValueError("credential=secret"), None]
    await apply_async(runtime)
    assert runtime.state == "failed"
    assert runtime.outcome == "initialization-failed"
    assert "secret" not in runtime.message
    await apply_async(runtime)
    assert runtime.state == "ready"


async def test_apply_survives_client_and_concurrent_apply_is_busy(runtime: RuntimeLifecycle) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    config = await runtime._load_async()

    async def initialize_async(**kwargs: object) -> None:
        entered.set()
        await release.wait()

    config.initialize_pyrit_async.side_effect = initialize_async
    _, version = await runtime.source.read_with_version_async()
    runtime.begin_apply(version=version, stop_scenarios=False, work_revision=None)
    await entered.wait()
    assert runtime.begin_apply(version=version, stop_scenarios=False, work_revision=None)["outcome"] == "busy"
    assert runtime.state == "initializing"
    release.set()
    await runtime.apply_task
    assert runtime.state == "ready"


async def test_slow_send_is_retained_after_http_cancellation_and_drain_timeout(runtime: RuntimeLifecycle) -> None:
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
        runtime.DRAIN_TIMEOUT_SECONDS = 0
        await apply_async(runtime, stop=True, revision=runtime.work_revision)
        assert runtime.state == "blocked"
        assert (await client.post("/api/attacks/id/messages")).status_code == 503
        assert (await client.get("/api/config")).status_code == 200
        assert (await client.get("/api/health")).status_code == 200
        release.set()
        await persisted.wait()
        await asyncio.gather(*runtime.operations)
        await apply_async(runtime)
        assert runtime.state == "ready"


async def test_background_estimates_block_reset(runtime: RuntimeLifecycle) -> None:
    runtime.DRAIN_TIMEOUT_SECONDS = 0
    with patch.object(lifecycle_module, "outstanding_estimates", return_value=2):
        await apply_async(runtime, stop=True, revision=0)
    assert runtime.state == "blocked"
    assert runtime.outcome == "stop-timeout"
    lifecycle_module.close_services_async.assert_not_awaited()


async def test_apply_denies_writes_but_allows_repair_reads(runtime: RuntimeLifecycle) -> None:
    async with runtime.edit_lock:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
            assert (await client.put("/api/config", json={})).status_code == 503
            assert (await client.get("/api/config")).status_code == 200
            assert runtime.begin_apply(version="v", stop_scenarios=False, work_revision=None)["outcome"] == "busy"


async def test_non_admin_apply_and_status_remain_denied(runtime: RuntimeLifecycle) -> None:
    runtime.app.dependency_overrides.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        assert (await client.get("/api/config/runtime")).status_code == 403
        assert (await client.post("/api/config/runtime/apply", json={"version": "v"})).status_code == 403


async def test_admin_can_apply_without_configuration_opt_in(runtime: RuntimeLifecycle) -> None:
    runtime.app.dependency_overrides.clear()

    @runtime.app.middleware("http")
    async def authenticate_async(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.user = AuthenticatedUser(
            oid="admin", name="Administrator", email="admin@example.com", groups=[], is_admin=True
        )
        return await call_next(request)

    _, version = await runtime.source.read_with_version_async()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        assert (await client.get("/api/config/runtime")).json()["enabled"]
        response = await client.post("/api/config/runtime/apply", json={"version": version})
        assert response.status_code == 202
    assert runtime.apply_task is not None
    await runtime.apply_task
    assert runtime.outcome == "success"


def test_authorization_policy_does_not_change_with_environment(runtime: RuntimeLifecycle) -> None:
    runtime.app.state.auth_environment["PYRIT_ALLOW_UNAUTHENTICATED_ADMIN"] = ""
    request = Request({"type": "http", "app": runtime.app})
    with patch.dict(os.environ, {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "true"}):
        with pytest.raises(Exception) as error:
            require_admin(request)
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    "worker_setting", ["WEB_CONCURRENCY", "UVICORN_WORKERS", "PYRIT_API_WORKERS", "PYRIT_REPLICAS"]
)
def test_multiple_workers_disable_apply(worker_setting: str, tmp_path: Path) -> None:
    with patch.dict(os.environ, {worker_setting: "2"}, clear=True):
        service = RuntimeLifecycle(app=FastAPI(), source=ConfigurationFileService(config_file_value=None))
    assert not service.enabled


async def test_invalid_cold_configuration_retains_raw_repair(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("broken: [", encoding="utf-8")
    source = ConfigurationFileService(config_file_value=str(path))
    app = FastAPI()
    service = RuntimeLifecycle(app=app, source=source)
    await service.startup_async()
    assert service.state == "failed"
    assert not app.state.allow_custom_initializers
    assert app.state.environment_file_service is None
    content, version = await source.read_with_version_async()
    assert content == "broken: ["
    await source.update_async("memory_db_type: in_memory\n", expected_version=version)
    assert "in_memory" in await source.read_async()


async def test_admitted_body_cannot_launch_after_stop_even_when_pending_apply_cancelled(
    runtime: RuntimeLifecycle,
) -> None:
    runtime.app.include_router(scenario_routes.router, prefix="/api")
    receiving, release = asyncio.Event(), asyncio.Event()

    async def body_async():
        receiving.set()
        await release.wait()
        yield b'{"scenario_name":"test","target_name":"target"}'

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        request = asyncio.create_task(
            client.post("/api/scenarios/runs", content=body_async(), headers={"Content-Type": "application/json"})
        )
        await receiving.wait()
        runtime.DRAIN_TIMEOUT_SECONDS = 0
        await apply_async(runtime, stop=True, revision=runtime.work_revision)
        assert runtime.state == "blocked"
        runtime.cancel_pending()
        with patch.object(scenario_routes, "get_scenario_run_service") as get_service:
            release.set()
            response = await request
            assert response.status_code == 503
            get_service.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("cold_failure", [False, True])
async def test_api_save_apply_target_and_script_failure_repair_without_restart(
    tmp_path: Path, cold_failure: bool
) -> None:
    from pyrit.memory import CentralMemory

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = """
from pyrit.setup.pyrit_initializer import PyRITInitializer
from pyrit.registry import TargetRegistry
from pyrit.prompt_target import TextTarget
class SavedInitializer(PyRITInitializer):
    @property
    def name(self): return "saved"
    @property
    def description(self): return "saved"
    @property
    def required_env_vars(self): return []
    async def initialize_async(self):
        TargetRegistry.get_registry_singleton().instances.register(TextTarget(), name="saved_target")
"""
    path = tmp_path / "config.yaml"
    content = (
        "memory_db_type: in_memory\nenv_files: []\ninitialization_scripts: []\n"
        f"allow_custom_initializers: true\ncustom_initializers_source: '{scripts}'\ninitializers: [saved]\n"
    )
    (scripts / "saved.py").write_text("raise RuntimeError('secret')" if cold_failure else script, encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    source = ConfigurationFileService(config_file_value=str(path))
    app = FastAPI()
    runtime = RuntimeLifecycle(app=app, source=source)
    app.state.runtime_lifecycle, app.state.configuration_file_service = runtime, source
    app.add_middleware(RuntimeAdmissionMiddleware)
    for router in (configuration.router, initializer_routes.router, target_routes.router):
        app.include_router(router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: None
    memory = CentralMemory.get_memory_instance()
    with patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH", tmp_path / "absent"):
        try:
            await runtime.startup_async()
            assert runtime.state == ("failed" if cold_failure else "ready")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                if cold_failure:
                    assert (await client.get("/api/config")).status_code == 200
                    assert (await client.delete("/api/initializers/saved")).status_code == 204
                    assert (
                        await client.post("/api/initializers", json={"name": "saved", "script_content": script})
                    ).status_code == 201
                response = await client.post(
                    "/api/initializers",
                    json={
                        "name": "replacement",
                        "script_content": script.replace("saved_target", "replacement_target"),
                    },
                )
                assert response.status_code == 201, response.text
                saved = (await client.get("/api/config")).json()
                response = await client.put(
                    "/api/config",
                    json={"version": saved["version"], "content": content.replace("[saved]", "[replacement]")},
                )
                assert response.status_code == 200, response.text
                version = response.json()["version"]
                assert (await client.post("/api/config/runtime/apply", json={"version": version})).status_code == 202
                await runtime.apply_task
                assert runtime.state == "ready"
                assert (await client.get("/api/targets/replacement_target")).status_code == 200
                assert (await client.get("/api/targets/saved_target")).status_code == 404
                (scripts / "replacement.py").write_text("raise RuntimeError('secret')", encoding="utf-8")
                await client.post("/api/config/runtime/apply", json={"version": version})
                await runtime.apply_task
                assert runtime.state == "failed"
                assert (await client.get("/api/targets/replacement_target")).status_code == 503
                assert (await client.get("/api/config")).status_code == 200
                assert (await client.get("/api/initializers/custom")).status_code == 200
                assert (await client.delete("/api/initializers/replacement")).status_code == 204
                assert (
                    await client.post(
                        "/api/initializers",
                        json={
                            "name": "replacement",
                            "script_content": script.replace("saved_target", "repaired_target"),
                        },
                    )
                ).status_code == 201
                await client.post("/api/config/runtime/apply", json={"version": version})
                await runtime.apply_task
                assert runtime.state == "ready"
                assert (await client.get("/api/targets/repaired_target")).status_code == 200
                assert CentralMemory.get_memory_instance() is memory
        finally:
            await runtime.shutdown_async()
            reset_setup_registries()
