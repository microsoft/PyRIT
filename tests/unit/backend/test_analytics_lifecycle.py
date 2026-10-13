# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Runtime ownership of native analytics work after its HTTP caller finishes."""

import asyncio
import os
from collections.abc import AsyncGenerator, Coroutine, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

import pyrit.backend.services.runtime_lifecycle as lifecycle_module
import pyrit.backend.services.service_lifecycle as services_module
from pyrit.analytics import AttackResultAnalytics
from pyrit.backend.main import app as backend_app
from pyrit.backend.main import lifespan
from pyrit.backend.middleware.compatibility import CompatibilityAPI
from pyrit.backend.middleware.error_handlers import register_error_handlers
from pyrit.backend.middleware.runtime import RuntimeAdmissionMiddleware
from pyrit.backend.routes.analytics import router
from pyrit.backend.services.analytics_service import close_analytics_service_async, get_analytics_service
from pyrit.backend.services.configuration_file_service import ConfigurationFileService
from pyrit.backend.services.runtime_lifecycle import RuntimeLifecycle
from pyrit.backend.services.service_lifecycle import close_services_async, has_active_analytics_work
from pyrit.exceptions.analytics_exception import AnalyticsBusyException
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.memory_models import Base
from pyrit.setup.configuration_loader import ConfigurationLoader


@pytest.fixture
def analytics_runtime(*, tmp_path: Path, analytics_service: AttackResultAnalytics) -> Iterator[RuntimeLifecycle]:
    path = tmp_path / "config.yaml"
    path.write_text("memory_db_type: in_memory\nenable_live_reinitialization: true\n", encoding="utf-8")
    app = FastAPI()
    runtime = RuntimeLifecycle(app=app, source=ConfigurationFileService(config_file_value=str(path)))
    runtime.state, runtime.generation = "ready", "original"
    app.state.runtime_lifecycle = runtime
    app.add_middleware(RuntimeAdmissionMiddleware)
    register_error_handlers(app)
    app.include_router(router, prefix="/api")
    config = ConfigurationLoader(memory_db_type="in_memory", env_files=[], enable_live_reinitialization=True)
    with (
        patch.object(runtime, "_load_async", return_value=config),
        patch.object(runtime, "_management_async", new_callable=AsyncMock),
        patch.object(config, "preflight_reinitialization_async", return_value=MagicMock()),
        patch.object(config, "apply_prepared_reinitialization_async", new_callable=AsyncMock),
        patch.object(lifecycle_module, "validate_reinitialization_memory"),
        patch.object(lifecycle_module, "resolve_environment_async", return_value={}),
        patch.object(lifecycle_module, "peek_scenario_run_service", return_value=None),
        patch.object(lifecycle_module, "outstanding_estimates", return_value=0),
    ):
        yield runtime


async def _apply_async(runtime: RuntimeLifecycle) -> None:
    _, version = await runtime.source.read_with_version_async()
    assert runtime.begin_apply(version=version)["outcome"] == "accepted"
    assert runtime.apply_task is not None
    await runtime.apply_task


@dataclass
class _Cleanup:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)


@pytest.fixture
async def held_native_cleanup(sqlite_instance: SQLiteMemory) -> AsyncGenerator[_Cleanup, None]:
    """Hold an actual native session open until the test explicitly releases cleanup."""
    state = _Cleanup()
    get_session = sqlite_instance.get_session_async
    with ExitStack() as patches:

        async def get_session_async() -> AsyncSession:
            session = await get_session()
            close = session.close

            async def close_async() -> None:
                state.entered.set()
                await state.release.wait()
                await close()
                state.closed.set()

            patches.enter_context(patch.object(session, "close", side_effect=close_async))
            return session

        patches.enter_context(patch.object(sqlite_instance, "get_session_async", side_effect=get_session_async))
        try:
            yield state
        finally:
            state.release.set()
            await close_analytics_service_async()


@pytest.fixture
async def replacement_memory(tmp_path: Path) -> AsyncGenerator[SQLiteMemory, None]:
    """Create a second private database without consulting the SQLite singleton or running migrations."""
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=tmp_path / "replacement.sqlite", skip_schema_migration=True, _defer_initialization=True)
    memory.results_path = str(tmp_path)
    try:
        async with await memory.get_session_async() as session:
            connection = await session.connection()
            await connection.run_sync(Base.metadata.create_all)
            await session.commit()
        yield memory
    finally:
        await memory.dispose_engine_async()


async def test_live_apply_closes_sdk_before_replacing_memory_async(
    *,
    analytics_runtime: RuntimeLifecycle,
    analytics_service: AttackResultAnalytics,
    sqlite_instance: SQLiteMemory,
    replacement_memory: SQLiteMemory,
) -> None:
    assert get_analytics_service() is analytics_service
    await analytics_service.query_async()
    config = await analytics_runtime._load_async()

    async def replace_async(**kwargs: object) -> None:
        assert analytics_service.is_closed
        assert get_analytics_service.cache_info().currsize == 0
        assert not analytics_service.has_active_work
        await sqlite_instance.dispose_engine_async()
        CentralMemory.set_memory_instance(replacement_memory)

    config.apply_prepared_reinitialization_async.side_effect = replace_async
    await _apply_async(analytics_runtime)
    assert analytics_runtime.state == "ready"
    assert analytics_runtime.generation != "original"
    replacement = get_analytics_service()
    assert replacement is not analytics_service
    assert replacement._memory is replacement_memory
    assert (await replacement.results_async()).items == []
    with pytest.raises(AnalyticsBusyException):
        await analytics_service.results_async()


@pytest.mark.parametrize("cancel_shutdown", [False, True])
async def test_http_timeout_keeps_native_cleanup_busy_until_runtime_drain_async(
    *,
    analytics_runtime: RuntimeLifecycle,
    analytics_service: AttackResultAnalytics,
    sqlite_instance: SQLiteMemory,
    held_native_cleanup: _Cleanup,
    cancel_shutdown: bool,
) -> None:
    expire, closing = asyncio.Event(), asyncio.Event()
    cleanup = held_native_cleanup

    async def expire_async(tasks: set[asyncio.Task[Any]], *, timeout: float) -> tuple[set[Any], set[Any]]:
        assert 0 < timeout <= 30
        await expire.wait()
        return set(), tasks

    original_close = analytics_service.close_async

    async def close_async() -> None:
        closing.set()
        await original_close()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=analytics_runtime.app), base_url="http://test"
    ) as client:
        with (
            patch("pyrit.analytics._execution.asyncio.wait", side_effect=expire_async),
            patch.object(analytics_service, "close_async", side_effect=close_async),
            patch.object(
                sqlite_instance, "dispose_engine_async", wraps=sqlite_instance.dispose_engine_async
            ) as dispose,
        ):
            request = asyncio.create_task(client.post("/api/analytics/attacks/results", json={}))
            shutdown: asyncio.Task[None] | None = None
            try:
                await asyncio.wait_for(cleanup.entered.wait(), 30)
                assert has_active_analytics_work()
                expire.set()
                response = await asyncio.wait_for(request, 10)
                assert response.status_code == 504
                assert not analytics_runtime.operations
                assert analytics_service.has_active_work
                await _apply_async(analytics_runtime)
                assert analytics_runtime.outcome == "busy"
                assert analytics_runtime.generation == "original"
                shutdown = asyncio.create_task(analytics_runtime.shutdown_async())
                await asyncio.wait_for(closing.wait(), 10)
                assert not shutdown.done()
                assert not analytics_service.is_closed
                assert get_analytics_service() is analytics_service
                dispose.assert_not_awaited()
                assert (await client.post("/api/analytics/attacks/results", json={})).status_code == 503
                if cancel_shutdown:
                    for _ in range(2):
                        shutdown.cancel()
                        await asyncio.sleep(0)
                        assert not shutdown.done()
                        dispose.assert_not_awaited()
                cleanup.release.set()
                if cancel_shutdown:
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(shutdown, 30)
                else:
                    await asyncio.wait_for(shutdown, 30)
                assert cleanup.closed.is_set()
                assert analytics_service.is_closed
                assert not has_active_analytics_work()
                assert get_analytics_service.cache_info().currsize == 0
                dispose.assert_awaited_once()
            finally:
                expire.set()
                cleanup.release.set()
                await asyncio.gather(request, *([shutdown] if shutdown else []), return_exceptions=True)


async def test_caller_and_repeated_close_cancellation_retain_native_owner_async(
    *, analytics_service: AttackResultAnalytics, held_native_cleanup: _Cleanup
) -> None:
    cleanup = held_native_cleanup
    request = asyncio.create_task(analytics_service.results_async())
    close: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(cleanup.entered.wait(), 30)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert has_active_analytics_work()
        close = asyncio.create_task(close_services_async())
        await asyncio.sleep(0)
        for _ in range(2):
            close.cancel()
            await asyncio.sleep(0)
            assert not close.done()
            assert get_analytics_service() is analytics_service
        cleanup.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(close, 30)
        assert cleanup.closed.is_set()
        assert analytics_service.is_closed
        assert get_analytics_service.cache_info().currsize == 0
    finally:
        cleanup.release.set()
        await asyncio.gather(request, *([close] if close else []), return_exceptions=True)


@pytest.mark.parametrize("operation", ["apply", "shutdown"])
async def test_failed_close_retains_owner_and_never_replaces_or_disposes_memory_async(
    *,
    analytics_runtime: RuntimeLifecycle,
    analytics_service: AttackResultAnalytics,
    sqlite_instance: SQLiteMemory,
    operation: str,
) -> None:
    await analytics_service.results_async()
    execution = analytics_service._execution()

    def reject(coroutine: Coroutine[Any, Any, Any]) -> None:
        coroutine.close()
        raise RuntimeError("Cannot schedule analytics drain")

    with (
        patch.object(execution, "_create_task", side_effect=reject),
        patch.object(sqlite_instance, "dispose_engine_async", new_callable=AsyncMock) as dispose,
    ):
        if operation == "apply":
            await _apply_async(analytics_runtime)
            assert analytics_runtime.state == "restart-required"
            config = await analytics_runtime._load_async()
            config.apply_prepared_reinitialization_async.assert_not_awaited()
        else:
            with pytest.raises(BaseExceptionGroup) as error:
                await analytics_runtime.shutdown_async()
            assert "Cannot schedule analytics drain" in str(error.value.exceptions[0])
            assert "memory was not disposed" in str(error.value.exceptions[1])
        dispose.assert_not_awaited()
        assert get_analytics_service() is analytics_service
        assert analytics_service.has_active_work
        assert not analytics_service.is_closed
        with pytest.raises(AnalyticsBusyException):
            await analytics_service.query_async()
    await close_analytics_service_async()
    assert analytics_service.is_closed
    assert get_analytics_service.cache_info().currsize == 0


@pytest.mark.parametrize("cancelled", [False, True])
async def test_other_service_failure_still_closes_analytics_async(
    *, analytics_service: AttackResultAnalytics, cancelled: bool
) -> None:
    await analytics_service.results_async()
    error = asyncio.CancelledError("manual send cleanup") if cancelled else RuntimeError("manual send cleanup")
    with patch.object(services_module, "_close_cached_services_async", side_effect=error):
        with pytest.raises(type(error)) as caught:
            await close_services_async()
        assert caught.value is error
    assert analytics_service.is_closed
    assert get_analytics_service.cache_info().currsize == 0


async def test_service_and_analytics_close_failures_are_both_reported_async(
    analytics_service: AttackResultAnalytics,
) -> None:
    await analytics_service.results_async()
    services_error = RuntimeError("Existing service closure failed")
    analytics_error = RuntimeError("Analytics closure failed")
    with (
        patch.object(services_module, "_close_cached_services_async", side_effect=services_error),
        patch.object(analytics_service, "close_async", side_effect=analytics_error),
    ):
        with pytest.raises(BaseExceptionGroup, match="Backend service closure failed") as caught:
            await close_services_async()
    assert caught.value.exceptions == (services_error, analytics_error)
    assert get_analytics_service() is analytics_service
    assert not analytics_service.is_closed


async def test_cancelled_apply_close_requires_restart_without_replacement_async(
    *, analytics_runtime: RuntimeLifecycle, analytics_service: AttackResultAnalytics
) -> None:
    await analytics_service.results_async()
    original_close = analytics_service.close_async

    async def cancelled_close_async() -> None:
        await original_close()
        raise asyncio.CancelledError

    with patch.object(analytics_service, "close_async", side_effect=cancelled_close_async):
        with pytest.raises(asyncio.CancelledError):
            await _apply_async(analytics_runtime)
    assert analytics_runtime.state == "restart-required"
    assert get_analytics_service.cache_info().currsize == 0
    config = await analytics_runtime._load_async()
    config.apply_prepared_reinitialization_async.assert_not_awaited()


async def test_unused_dependency_is_not_constructed_by_lifecycle_async() -> None:
    assert get_analytics_service.cache_info().currsize == 0
    with patch("pyrit.backend.services.analytics_service.AttackResultAnalytics") as constructor:
        assert not has_active_analytics_work()
        await close_analytics_service_async()
    constructor.assert_not_called()


async def test_actual_app_lifespan_restarts_with_a_fresh_native_sdk_async(
    *,
    tmp_path: Path,
    sqlite_instance: SQLiteMemory,
    analytics_service: AttackResultAnalytics,
    compatibility_headers: dict[str, str],
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("memory_db_type: in_memory\nenv_files: []\n", encoding="utf-8")
    app = CompatibilityAPI(
        routes=list(backend_app.router.routes),
        middleware=backend_app.user_middleware,
        exception_handlers=backend_app.exception_handlers,
    )
    owners = []
    with (
        patch.dict(
            os.environ,
            {
                "PYRIT_CONFIG_FILE": str(path),
                "ENTRA_CLIENT_ID": "",
                "ENTRA_TENANT_ID": "",
                "ENTRA_ALLOWED_GROUP_IDS": "",
            },
        ),
        patch.object(ConfigurationLoader, "initialize_pyrit_async", new_callable=AsyncMock),
        patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH", tmp_path / "missing-default.yaml"),
        patch("pyrit.backend.main.setup_frontend"),
        patch.object(sqlite_instance, "dispose_engine_async", new_callable=AsyncMock) as dispose,
    ):
        for _ in range(2):
            async with lifespan(app):
                assert app.state.runtime_lifecycle.state == "ready"
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                    headers=compatibility_headers,
                ) as client:
                    response = await client.post("/api/analytics/attacks/query", json={})
                assert response.status_code == 200
                owners.append(get_analytics_service())
            assert owners[-1].is_closed
            assert get_analytics_service.cache_info().currsize == 0
    assert owners[0] is not owners[1]
    assert dispose.await_count == 2
