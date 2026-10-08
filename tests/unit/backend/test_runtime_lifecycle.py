# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Admission and fail-fast runtime replacement invariants."""

import asyncio
import os
from collections.abc import Generator
from contextlib import ExitStack
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

import pyrit.backend.services.runtime_lifecycle as lifecycle_module
from pyrit.backend.middleware.auth import require_admin
from pyrit.backend.middleware.runtime import RuntimeAdmissionMiddleware
from pyrit.backend.routes import configuration, health
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.manual_send_scheduler import get_manual_send_scheduler
from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
from pyrit.backend.services.scenario_run_service import ScenarioRunService, _PreparedRun
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import ScenarioRunState
from pyrit.models.catalog.scenario import RunScenarioRequest
from pyrit.scenario import Scenario
from pyrit.setup.configuration_loader import ConfigurationLoader
from unit.mocks import make_scenario_result


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
        patch.object(CentralMemory, "_memory_instance", None),
    ):
        yield service


async def apply_async(runtime: RuntimeLifecycle) -> None:
    _, version = await runtime.source.read_with_version_async()
    result = runtime.begin_apply(version=version)
    assert result["outcome"] == "accepted"
    assert runtime.apply_task is not None
    await runtime.apply_task


@pytest.mark.parametrize("state", ["initializing", "ready", "failed", "restart-required", None])
async def test_runtime_readiness_reports_state_async(*, runtime: RuntimeLifecycle, state: str | None) -> None:
    if state is None:
        del runtime.app.state.runtime_lifecycle
    else:
        runtime.state = state
        assert runtime.status()["state"] == state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        response = await client.get("/api/runtime")
    assert response.status_code == 200
    assert response.json() == {
        "ready": state == "ready",
        "state": state if state is not None else "failed",
        "generation": "original" if state is not None else "",
    }


@pytest.mark.parametrize(
    "failures", [(), ("scenarios",), ("services",), ("memory",), ("scenarios", "services", "memory")]
)
@pytest.mark.parametrize("cancelled", [False, True])
async def test_shutdown_disposes_memory_after_services_even_on_failure(
    *, runtime: RuntimeLifecycle, failures: tuple[str, ...], cancelled: bool
) -> None:
    memory = MagicMock(spec=MemoryInterface)
    scenario_service = MagicMock(spec=ScenarioRunService)
    order = MagicMock()
    with (
        patch.object(CentralMemory, "_memory_instance", memory),
        patch.object(lifecycle_module, "peek_scenario_run_service", return_value=scenario_service),
        patch.object(lifecycle_module, "close_services_async", new_callable=AsyncMock) as close_services,
    ):
        order.attach_mock(scenario_service.shutdown_async, "scenarios")
        order.attach_mock(close_services, "services")
        order.attach_mock(memory.dispose_engine_async, "memory")
        errors = tuple(asyncio.CancelledError(name) if cancelled else RuntimeError(name) for name in failures)
        for name, error in zip(failures, errors, strict=True):
            getattr(order, name).side_effect = error
        if len(errors) == 1:
            with pytest.raises(type(errors[0])) as single_error:
                await runtime.shutdown_async()
            assert single_error.value is errors[0]
        elif errors:
            with pytest.raises(BaseExceptionGroup) as caught:
                await runtime.shutdown_async()
            assert caught.value.exceptions == errors
        else:
            await runtime.shutdown_async()

    assert order.mock_calls == [call.scenarios(), call.services(), call.memory()]


async def test_shutdown_repeated_failure_preserves_original_error_async(runtime: RuntimeLifecycle) -> None:
    failure = RuntimeError("service shutdown failed")
    lifecycle_module.close_services_async.side_effect = failure

    for _ in range(2):
        with pytest.raises(RuntimeError, match="service shutdown failed") as caught:
            await runtime.shutdown_async()
        assert caught.value is failure
        assert runtime.is_stopping
        lifecycle_module.close_services_async.assert_awaited_once()


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
    service.has_active_work.return_value = True
    with patch.object(lifecycle_module, "peek_scenario_run_service", return_value=service):
        await apply_async(runtime)
    assert runtime.outcome == "busy"
    assert runtime.state == "ready"
    lifecycle_module.close_services_async.assert_not_awaited()
    assert not hasattr(service, "request_stop") or not service.request_stop.called


@pytest.mark.parametrize("count", [1, 3])
async def test_admitted_manual_send_blocks_reinitialization_without_an_http_request_async(
    runtime: RuntimeLifecycle,
    count: int,
) -> None:
    get_manual_send_scheduler.cache_clear()
    try:
        scheduler = get_manual_send_scheduler()
        with ExitStack() as reservations:
            source = reservations.enter_context(ExitStack())
            source.enter_context(scheduler.reserve(conversation_id="accepted-send"))
            for index in range(count - 1):
                reservations.enter_context(scheduler.reserve(conversation_id=f"copy-{index}"))
            assert not runtime.operations
            await apply_async(runtime)
            assert runtime.outcome == "busy"
            assert runtime.generation == "original"
            lifecycle_module.close_services_async.assert_not_awaited()
            if count > 1:
                source.close()
                await apply_async(runtime)
                assert runtime.outcome == "busy"
                assert runtime.generation == "original"
        await apply_async(runtime)
        assert runtime.generation != "original"
    finally:
        get_manual_send_scheduler.cache_clear()


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
        assert len(runtime.operations) == 1
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


async def test_http_disconnect_retains_scenario_launch_async(runtime: RuntimeLifecycle) -> None:
    """HTTP cancellation leaves the middleware-owned service waiter alive."""
    memory = MagicMock(spec=MemoryInterface)
    record = make_scenario_result(attack_results={}, scenario_run_state=ScenarioRunState.CREATED)
    memory.get_scenario_results_async.return_value = [record]
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        service = ScenarioRunService()
    scenario = MagicMock(spec=Scenario)
    scenario._scenario_result_id = str(record.id)
    scenario.active_atomic_group_ids = set()
    entered, release, executed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def prepare_async(*, request: RunScenarioRequest) -> _PreparedRun:
        entered.set()
        await release.wait()
        return _PreparedRun(scenario=scenario)

    async def run_async() -> None:
        executed.set()
        record.scenario_run_state = ScenarioRunState.COMPLETED

    scenario.run_async = AsyncMock(side_effect=run_async)

    @runtime.app.post("/api/scenarios/runs")
    async def launch_async(request: RunScenarioRequest) -> dict[str, str]:
        result = await service.start_run_async(request=request)
        return {"scenario_result_id": result.scenario_result_id}

    with (
        patch.object(service, "_prepare_run_async", side_effect=prepare_async),
        patch.object(lifecycle_module, "peek_scenario_run_service", return_value=service),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
            request = asyncio.create_task(
                client.post("/api/scenarios/runs", json={"scenario_name": "test", "target_name": "test"})
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request, 5)
                assert runtime.operations
                assert service.has_active_work()
                assert not service._abandoned_prepare_tasks
                await apply_async(runtime)
                assert runtime.outcome == "busy"
                release.set()
                await asyncio.wait_for(asyncio.gather(*runtime.operations), 5)
                await asyncio.wait_for(executed.wait(), 5)
                scenario.run_async.assert_awaited_once()
                assert not service._abandoned_prepare_tasks
            finally:
                release.set()
                await asyncio.gather(request, *runtime.operations, return_exceptions=True)
                await asyncio.wait_for(service.shutdown_async(), 5)


@pytest.mark.parametrize("phase", ["preparation", "cleanup"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("cancel_shutdown", [False, True])
async def test_runtime_retains_abandoned_preparation_until_memory_shutdown_async(
    *, runtime: RuntimeLifecycle, phase: str, cleanup_fails: bool, cancel_shutdown: bool
) -> None:
    memory = MagicMock(spec=MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        service = ScenarioRunService()
    scenario = MagicMock(spec=Scenario)
    scenario._scenario_result_id = "abandoned"
    entered, release, cleanup_entered, cleanup_release, stopped = (asyncio.Event() for _ in range(5))
    order: list[str] = []
    original_stop = service.stop_admission

    def stop() -> None:
        original_stop()
        stopped.set()

    async def prepare_async(*, request: RunScenarioRequest) -> _PreparedRun:
        entered.set()
        await release.wait()
        order.append("prepared")
        return _PreparedRun(scenario=scenario)

    async def cleanup_async(**kwargs: object) -> bool:
        assert kwargs["expected_states"] == {
            ScenarioRunState.CREATED,
            ScenarioRunState.IN_PROGRESS,
            ScenarioRunState.QUEUED,
        }
        cleanup_entered.set()
        await cleanup_release.wait()
        order.append("cleaned")
        if cleanup_fails:
            raise RuntimeError("cleanup persistence failed")
        return True

    async def dispose_async() -> None:
        assert not service.has_active_work()
        order.append("memory-closed")

    memory.try_update_scenario_run_state_async.side_effect = cleanup_async
    memory.dispose_engine_async.side_effect = dispose_async
    with (
        patch.object(service, "_prepare_run_async", side_effect=prepare_async) as prepare,
        patch.object(service, "stop_admission", side_effect=stop),
        patch.object(lifecycle_module, "peek_scenario_run_service", return_value=service),
        patch.object(CentralMemory, "_memory_instance", memory),
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
    ):
        start = asyncio.create_task(
            service.start_run_async(request=RunScenarioRequest(scenario_name="test", target_name="test"))
        )
        shutdown: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await apply_async(runtime)
            assert runtime.outcome == "busy"
            start.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(start, 5)
            if phase == "cleanup":
                release.set()
                await asyncio.wait_for(cleanup_entered.wait(), 5)
            await apply_async(runtime)
            assert runtime.outcome == "busy"
            lifecycle_module.close_services_async.assert_not_awaited()
            shutdown = asyncio.create_task(runtime.shutdown_async())
            await asyncio.wait_for(stopped.wait(), 5)
            assert service._stopping
            assert not shutdown.done()
            memory.dispose_engine_async.assert_not_awaited()
            with pytest.raises(RuntimeError, match="scheduling is stopping"):
                await service.start_run_async(request=RunScenarioRequest(scenario_name="test", target_name="test"))
            prepare.assert_awaited_once()
            if cancel_shutdown:
                shutdown.cancel()
                barrier = asyncio.Event()
                asyncio.get_running_loop().call_soon(barrier.set)
                await asyncio.wait_for(barrier.wait(), 5)
                assert not shutdown.done()
                assert all(task.cancelling() == 0 for task in service._preparations)
            release.set()
            await asyncio.wait_for(cleanup_entered.wait(), 5)
            assert not shutdown.done()
            memory.dispose_engine_async.assert_not_awaited()
            cleanup_release.set()
            if cleanup_fails:
                with pytest.raises(ExceptionGroup, match="shutdown transitions") as error:
                    await asyncio.wait_for(shutdown, 5)
                assert str(error.value.exceptions[0]) == "cleanup persistence failed"
            elif cancel_shutdown:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(shutdown, 5)
            else:
                await asyncio.wait_for(shutdown, 5)
            assert order == ["prepared", "cleaned", "memory-closed"]
            scenario.run_async.assert_not_called()
        finally:
            release.set()
            cleanup_release.set()
            await asyncio.gather(start, *([shutdown] if shutdown else []), return_exceptions=True)


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


@pytest.mark.parametrize("path", ["/api/attacks/id/messages", "/api/config/audit-write"])
@pytest.mark.parametrize("cancel_shutdown", [False, True])
@pytest.mark.parametrize("child_fails", [False, True])
async def test_shutdown_drains_disconnected_requests_before_closing_async(
    *, runtime: RuntimeLifecycle, path: str, cancel_shutdown: bool, child_fails: bool
) -> None:
    entered, shutdown_entered = asyncio.Event(), asyncio.Event()
    release = Event()
    events: list[str] = []
    failure = ValueError("retained request failed")

    @runtime.app.post(path)
    async def retained_write_async() -> dict[str, bool]:
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        events.append("request-settled")
        if child_fails:
            raise failure
        return {"stored": True}

    async def close_async() -> None:
        assert not runtime.operations and not runtime.management_operations
        events.append("resources-closed")

    async def shutdown_async() -> None:
        shutdown_entered.set()
        await runtime.shutdown_async()

    lifecycle_module.close_services_async.side_effect = close_async
    children: list[asyncio.Task[None]] = []
    shutdown: asyncio.Task[None] | None = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        request = asyncio.create_task(client.post(path))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            children = list(runtime.operations | runtime.management_operations)
            assert len(children) == 1
            shutdown = asyncio.create_task(shutdown_async())
            await asyncio.wait_for(shutdown_entered.wait(), timeout=5)
            lifecycle_module.close_services_async.assert_not_awaited()
            assert not shutdown.done()
            assert runtime.state == "ready"
            assert runtime.status()["state"] == "stopping"
            readiness = await client.get("/api/runtime")
            assert readiness.status_code == 200
            assert readiness.json() == {"ready": False, "state": "stopping", "generation": "original"}
            liveness = await client.get("/api/health")
            assert liveness.status_code == 200
            assert liveness.json()["status"] == "healthy"
            assert (await client.post(path)).status_code == 503
            assert (await client.get("/api/config")).status_code == 503
            assert runtime.begin_apply(version="ignored")["outcome"] == "stopping"
            assert runtime.apply_task is None
            if cancel_shutdown:
                for _ in range(2):
                    shutdown.cancel()
                    barrier = asyncio.Event()
                    asyncio.get_running_loop().call_soon(barrier.set)
                    await asyncio.wait_for(barrier.wait(), timeout=5)
                    assert not shutdown.done()
                    assert children[0].cancelling() == 0
                    lifecycle_module.close_services_async.assert_not_awaited()
            release.set()
            if child_fails:
                with pytest.raises(ValueError, match="retained request failed") as error:
                    await asyncio.wait_for(shutdown, timeout=5)
                assert error.value is failure
            elif cancel_shutdown:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(shutdown, timeout=5)
            else:
                await asyncio.wait_for(shutdown, timeout=5)
            assert events == ["request-settled", "resources-closed"]
            assert all(task.done() for task in children)
            assert not runtime.operations and not runtime.management_operations
            lifecycle_module.close_services_async.assert_awaited_once()
            if not child_fails:
                await runtime.shutdown_async()
                lifecycle_module.close_services_async.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(request, *children, *([shutdown] if shutdown else []), return_exceptions=True)


async def test_shutdown_preserves_request_and_close_failures_async(runtime: RuntimeLifecycle) -> None:
    entered, release, shutdown_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    request_error, close_error = ValueError("request cause"), RuntimeError("close cause")

    @runtime.app.post("/api/attacks/id/messages")
    async def failed_write_async() -> None:
        entered.set()
        await release.wait()
        raise request_error

    async def shutdown_async() -> None:
        shutdown_entered.set()
        await runtime.shutdown_async()

    lifecycle_module.close_services_async.side_effect = close_error
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        request = asyncio.create_task(client.post("/api/attacks/id/messages"))
        shutdown: asyncio.Task[None] | None = None
        children: list[asyncio.Task[None]] = []
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            children = list(runtime.operations)
            shutdown = asyncio.create_task(shutdown_async())
            await asyncio.wait_for(shutdown_entered.wait(), timeout=5)
            release.set()
            with pytest.raises(BaseExceptionGroup) as error:
                await asyncio.wait_for(shutdown, timeout=5)
            assert error.value.exceptions == (request_error, close_error)
            lifecycle_module.close_services_async.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(request, *children, *([shutdown] if shutdown else []), return_exceptions=True)


async def test_shutdown_drains_sibling_after_request_failure_async(runtime: RuntimeLifecycle) -> None:
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    shutdown_entered, failed = asyncio.Event(), asyncio.Event()
    failure = ValueError("first request failed")

    @runtime.app.post("/api/attacks/id/messages/{index}")
    async def write_async(index: int) -> None:
        entered[index].set()
        await release[index].wait()
        if index == 0:
            raise failure

    async def shutdown_async() -> None:
        shutdown_entered.set()
        await runtime.shutdown_async()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        requests = [asyncio.create_task(client.post(f"/api/attacks/id/messages/{index}")) for index in range(2)]
        shutdown: asyncio.Task[None] | None = None
        children: list[asyncio.Task[None]] = []
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), timeout=5)
            children = list(runtime.operations)
            assert len(children) == 2
            for task in children:
                task.add_done_callback(lambda _: failed.set())
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            shutdown = asyncio.create_task(shutdown_async())
            await asyncio.wait_for(shutdown_entered.wait(), timeout=5)
            release[0].set()
            await asyncio.wait_for(failed.wait(), timeout=5)
            assert not shutdown.done()
            assert len(runtime.operations) == 1
            lifecycle_module.close_services_async.assert_not_awaited()
            release[1].set()
            with pytest.raises(ValueError, match="first request failed") as error:
                await asyncio.wait_for(shutdown, timeout=5)
            assert error.value is failure
            assert all(task.done() for task in children)
            lifecycle_module.close_services_async.assert_awaited_once()
        finally:
            for event in release:
                event.set()
            await asyncio.gather(*requests, *children, *([shutdown] if shutdown else []), return_exceptions=True)


async def test_shutdown_keeps_admission_closed_while_accepted_apply_finishes_async(runtime: RuntimeLifecycle) -> None:
    entered, release, shutdown_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    request_entered, release_request = asyncio.Event(), asyncio.Event()
    config = await runtime._load_async()

    @runtime.app.get("/api/config/draining")
    async def retained_read_async() -> None:
        request_entered.set()
        await release_request.wait()

    async def initialize_async(**kwargs: object) -> None:
        entered.set()
        await release.wait()

    async def shutdown_async() -> None:
        shutdown_entered.set()
        await runtime.shutdown_async()

    config.apply_prepared_reinitialization_async.side_effect = initialize_async
    _, version = await runtime.source.read_with_version_async()
    runtime.begin_apply(version=version)
    shutdown: asyncio.Task[None] | None = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://test") as client:
        request = asyncio.create_task(client.get("/api/config/draining"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            await asyncio.wait_for(request_entered.wait(), timeout=5)
            shutdown = asyncio.create_task(shutdown_async())
            await asyncio.wait_for(shutdown_entered.wait(), timeout=5)
            assert runtime.state == "initializing"
            assert runtime.status()["state"] == "stopping"
            assert runtime.begin_apply(version=version)["outcome"] == "stopping"
            assert (await client.post("/api/config/runtime/apply", json={"version": version})).status_code == 503
            lifecycle_module.close_services_async.assert_awaited_once()
            release.set()
            assert runtime.apply_task is not None
            await asyncio.wait_for(runtime.apply_task, timeout=5)
            assert runtime.state == "ready"
            assert runtime.generation != "original"
            assert not shutdown.done()
            assert runtime.status()["state"] == "stopping"
            readiness = await client.get("/api/runtime")
            assert readiness.status_code == 200
            assert readiness.json() == {"ready": False, "state": "stopping", "generation": runtime.generation}
            assert (await client.get("/api/config")).status_code == 503
            release_request.set()
            await asyncio.wait_for(shutdown, timeout=5)
            assert runtime.status()["state"] == "stopping"
            assert lifecycle_module.close_services_async.await_count == 2
        finally:
            release.set()
            release_request.set()
            await asyncio.gather(
                request,
                *([runtime.apply_task] if runtime.apply_task else []),
                *([shutdown] if shutdown else []),
                return_exceptions=True,
            )


async def test_shutdown_cancellation_before_entry_and_during_close_async(runtime: RuntimeLifecycle) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def close_async() -> None:
        entered.set()
        await release.wait()

    lifecycle_module.close_services_async.side_effect = close_async
    cancelled = asyncio.create_task(runtime.shutdown_async())
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert not runtime.is_stopping
    shutdown = asyncio.create_task(runtime.shutdown_async())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        for _ in range(2):
            shutdown.cancel()
            barrier = asyncio.Event()
            asyncio.get_running_loop().call_soon(barrier.set)
            await asyncio.wait_for(barrier.wait(), timeout=5)
            assert not shutdown.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(shutdown, timeout=5)
        await runtime.shutdown_async()
        lifecycle_module.close_services_async.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(shutdown, return_exceptions=True)
