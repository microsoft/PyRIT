# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import jwt
import pytest

from pyrit.auth import (
    BrowserSessionCopilotAuthenticator,
)
from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

_TEST_JWT_KEY = "a" * 32


def _run_browser_lifetime_case(*, case: str, parameters: dict[str, str | bool]) -> None:
    # In-process cancellation deadlines cannot stop a broken completion acknowledgement.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import asyncio, json, runpy, sys; "
                "module = runpy.run_path(sys.argv[1]); "
                "asyncio.run(module[sys.argv[2]](**json.loads(sys.argv[3])))"
            ),
            str(Path(__file__).resolve()),
            case,
            json.dumps(parameters),
        ],
        cwd=Path(__file__).resolve().parents[3],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("case", "parameters"),
    [
        pytest.param(
            "_check_token_cancellation_drains_before_close_async", {"phase": phase}, id=f"token-cancellation-{phase}"
        )
        for phase in ("setup", "page", "navigation", "wait")
    ]
    + [
        pytest.param(
            "_check_close_cancellation_drains_cleanup_async",
            {"cleanup_fails": cleanup_fails},
            id=f"close-cancellation-cleanup-fails-{cleanup_fails}",
        )
        for cleanup_fails in (False, True)
    ]
    + [
        pytest.param("_check_token_cancellation_chains_cleanup_error_async", {}, id="token-cancellation-cleanup-error"),
        pytest.param(
            "_check_operation_error_preserves_cleanup_semantics_async",
            {"cleanup_fails": False},
            id="operation-error",
        ),
        pytest.param(
            "_check_operation_error_preserves_cleanup_semantics_async",
            {"cleanup_fails": True},
            id="operation-and-cleanup-error",
        ),
        pytest.param("_check_cancellation_during_error_cleanup_drains_async", {}, id="cancel-during-error-cleanup"),
        pytest.param(
            "_check_token_cancellation_before_browser_coroutine_entry_is_acknowledged_async",
            {},
            id="cancel-before-coroutine-entry",
        ),
        pytest.param("_check_close_cancellation_waits_for_thread_stop_async", {}, id="cancel-during-thread-stop"),
        pytest.param("_check_token_cancellation_waits_for_browser_startup_async", {}, id="cancel-during-thread-start"),
        pytest.param(
            "_check_startup_cancellation_and_close_wait_until_browser_loop_runs_async",
            {},
            id="cancel-before-loop-readiness",
        ),
    ]
    + [
        pytest.param(
            "_check_queued_close_cancellation_drains_capture_and_cleanup_async",
            {"cleanup_fails": cleanup_fails},
            id=f"queued-close-cancellation-cleanup-fails-{cleanup_fails}",
        )
        for cleanup_fails in (False, True)
    ],
)
def test_public_browser_lifetime(*, case: str, parameters: dict[str, str | bool]) -> None:
    _run_browser_lifetime_case(case=case, parameters=parameters)


class _BrowserLifetimeHarness:
    def __init__(self, *, phase: str, cleanup_error: Exception | None = None) -> None:
        self.phase = phase
        self.cleanup_error = cleanup_error
        self.operation_error = ValueError("navigation failed")
        self.main_loop = asyncio.get_running_loop()
        self.operation_started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_finished = threading.Event()
        self.cleanup_release = asyncio.Event()
        self.operation_task: asyncio.Task[Any] | None = None
        self.tasks: list[asyncio.Task[Any]] = []
        self.stopped_before_cleanup = False
        self.cleanup_cancellations = 0
        self.authenticator = BrowserSessionCopilotAuthenticator(headless=True)
        self.page = MagicMock()
        self.page.goto = AsyncMock(side_effect=self._navigate_async)
        self.context = MagicMock()
        self.context.pages = [] if phase == "page" else [self.page]
        self.context.new_page = AsyncMock(side_effect=self._new_page_async)
        self.context.close = AsyncMock(side_effect=self._close_context_async)
        self.playwright = MagicMock()
        self.playwright.chromium.launch_persistent_context = AsyncMock(side_effect=self._launch_async)
        self.manager = AsyncMock()
        self.manager.__aenter__.return_value = self.playwright
        self.manager.__aexit__.side_effect = self._exit_async

    async def _block_operation_async(self) -> None:
        self.operation_task = asyncio.current_task()
        self.main_loop.call_soon_threadsafe(self.operation_started.set)
        await asyncio.Event().wait()

    async def _launch_async(self, **kwargs: Any) -> MagicMock:
        if self.phase == "setup":
            await self._block_operation_async()
        return self.context

    async def _new_page_async(self) -> MagicMock:
        await self._block_operation_async()
        return self.page

    async def _navigate_async(self, url: str) -> None:
        self.operation_task = asyncio.current_task()
        if self.phase == "navigation":
            await self._block_operation_async()
        elif self.phase == "error":
            raise self.operation_error
        elif self.phase == "wait":
            self.main_loop.call_soon_threadsafe(self.operation_started.set)
        else:
            callback = self.page.on.call_args.args[1]
            callback(
                MagicMock(url=f"{self.authenticator.DEFAULT_WEBSOCKET_BASE_URL}/ChatHub?access_token={_make_token()}")
            )

    async def _block_cleanup_async(self) -> None:
        self.main_loop.call_soon_threadsafe(self.cleanup_started.set)
        try:
            await self.cleanup_release.wait()
        except asyncio.CancelledError:
            self.cleanup_cancellations += 1
            raise
        self.cleanup_finished.set()
        if self.cleanup_error is not None:
            raise self.cleanup_error

    async def _close_context_async(self) -> None:
        await self._block_cleanup_async()

    async def _exit_async(self, *args: Any) -> None:
        if self.phase == "setup":
            await self._block_cleanup_async()

    def release_cleanup(self) -> None:
        browser_loop = self.authenticator._browser_loop
        if browser_loop is not None:
            browser_loop.call_soon_threadsafe(self.cleanup_release.set)

    async def drain_browser_async(self) -> None:
        if self.operation_task is not None:
            await asyncio.gather(self.operation_task, return_exceptions=True)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


async def _event_loop_barrier_async() -> None:
    loop = asyncio.get_running_loop()
    reached = loop.create_future()
    loop.call_soon(reached.set_result, None)
    await reached


@asynccontextmanager
async def _browser_lifetime_async(
    *,
    phase: str,
    cleanup_error: Exception | None = None,
) -> AsyncIterator[_BrowserLifetimeHarness]:
    harness = _BrowserLifetimeHarness(phase=phase, cleanup_error=cleanup_error)
    authenticator = harness.authenticator
    original_stop = authenticator._stop_browser_thread_async
    original_tasks = asyncio.all_tasks()

    async def stop_async() -> None:
        harness.stopped_before_cleanup |= not harness.cleanup_finished.is_set()
        harness.release_cleanup()
        browser_loop = authenticator._browser_loop
        thread = authenticator._browser_thread
        if browser_loop is not None:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(harness.drain_browser_async(), browser_loop))
        await original_stop()
        if thread is not None:
            assert not thread.is_alive()

    with (
        patch.object(authenticator, "_create_playwright_context_manager", return_value=harness.manager),
        patch.object(authenticator, "_stop_browser_thread_async", side_effect=stop_async),
    ):
        try:
            yield harness
        finally:
            harness.release_cleanup()
            if not harness.cleanup_started.is_set():
                for task in harness.tasks:
                    if not task.done():
                        task.cancel()
            await asyncio.wait_for(asyncio.gather(*harness.tasks, return_exceptions=True), timeout=5)
            await asyncio.wait_for(authenticator.close_async(), timeout=5)
            assert authenticator._browser_thread is None
            assert authenticator._browser_loop is None
            assert not (asyncio.all_tasks() - original_tasks)


async def _check_token_cancellation_drains_before_close_async(phase: str) -> None:
    async with _browser_lifetime_async(phase=phase) as harness:
        authenticator = harness.authenticator
        caller = asyncio.create_task(authenticator.get_token_async())
        harness.tasks.append(caller)
        await asyncio.wait_for(harness.operation_started.wait(), timeout=5)
        caller.cancel("original cancellation")
        await asyncio.wait_for(harness.cleanup_started.wait(), timeout=5)
        closer = asyncio.create_task(authenticator.close_async())
        harness.tasks.append(closer)
        await _event_loop_barrier_async()

        assert not caller.done()
        assert not closer.done()
        assert authenticator._token_fetch_lock.locked()
        caller.cancel("repeated cancellation")
        await _event_loop_barrier_async()
        assert not caller.done()
        assert not harness.stopped_before_cleanup
        assert harness.cleanup_cancellations == 0
        assert authenticator._access_token is None

        harness.release_cleanup()
        with pytest.raises(asyncio.CancelledError, match="original cancellation"):
            await asyncio.wait_for(caller, timeout=5)
        await asyncio.wait_for(closer, timeout=5)
        assert harness.cleanup_finished.is_set()
        assert not harness.stopped_before_cleanup
        harness.manager.__aexit__.assert_awaited_once()
        assert harness.context.close.await_count == (0 if phase == "setup" else 1)
        assert harness.page.remove_listener.call_count == (0 if phase in {"setup", "page"} else 1)
        assert await authenticator.get_claims_async() == {}


async def _check_close_cancellation_drains_cleanup_async(cleanup_fails: bool) -> None:
    cleanup_error = RuntimeError("close cleanup failed") if cleanup_fails else None
    async with _browser_lifetime_async(phase="close", cleanup_error=cleanup_error) as harness:
        authenticator = harness.authenticator
        assert await authenticator.get_token_async()
        closer = asyncio.create_task(authenticator.close_async())
        harness.tasks.append(closer)
        await asyncio.wait_for(harness.cleanup_started.wait(), timeout=5)
        closer.cancel("original close cancellation")
        await _event_loop_barrier_async()
        closer.cancel("repeated close cancellation")
        await _event_loop_barrier_async()

        assert not closer.done()
        assert not harness.stopped_before_cleanup
        assert harness.cleanup_cancellations == 0
        assert authenticator._access_token is None
        harness.release_cleanup()
        with pytest.raises(asyncio.CancelledError, match="original close cancellation") as raised:
            await asyncio.wait_for(closer, timeout=5)
        assert raised.value.__cause__ is cleanup_error
        assert harness.cleanup_finished.is_set()
        assert not harness.stopped_before_cleanup
        harness.context.close.assert_awaited_once()
        harness.manager.__aexit__.assert_awaited_once()


async def _check_token_cancellation_chains_cleanup_error_async() -> None:
    cleanup_error = RuntimeError("cleanup failed")
    async with _browser_lifetime_async(phase="navigation", cleanup_error=cleanup_error) as harness:
        caller = asyncio.create_task(harness.authenticator.get_token_async())
        harness.tasks.append(caller)
        await asyncio.wait_for(harness.operation_started.wait(), timeout=5)
        caller.cancel("original cancellation")
        await asyncio.wait_for(harness.cleanup_started.wait(), timeout=5)
        caller.cancel("repeated cancellation")
        harness.release_cleanup()

        with pytest.raises(asyncio.CancelledError, match="original cancellation") as raised:
            await asyncio.wait_for(caller, timeout=5)
        assert raised.value.__cause__ is cleanup_error
        assert harness.cleanup_finished.is_set()
        harness.page.remove_listener.assert_called_once()
        harness.manager.__aexit__.assert_awaited_once()


async def _check_operation_error_preserves_cleanup_semantics_async(cleanup_fails: bool) -> None:
    cleanup_error = RuntimeError("cleanup failed") if cleanup_fails else None
    async with _browser_lifetime_async(phase="error", cleanup_error=cleanup_error) as harness:
        caller = asyncio.create_task(harness.authenticator.get_token_async())
        harness.tasks.append(caller)
        await asyncio.wait_for(harness.cleanup_started.wait(), timeout=5)
        assert not caller.done()
        harness.release_cleanup()
        with pytest.raises((ValueError, RuntimeError)) as raised:
            await asyncio.wait_for(caller, timeout=5)
        assert raised.value is (cleanup_error or harness.operation_error)
        assert harness.cleanup_finished.is_set()
        harness.page.remove_listener.assert_called_once()
        harness.manager.__aexit__.assert_awaited_once()


async def _check_cancellation_during_error_cleanup_drains_async() -> None:
    async with _browser_lifetime_async(phase="error") as harness:
        caller = asyncio.create_task(harness.authenticator.get_token_async())
        harness.tasks.append(caller)
        await asyncio.wait_for(harness.cleanup_started.wait(), timeout=5)
        caller.cancel("cancel during cleanup")
        await _event_loop_barrier_async()
        caller.cancel("repeat during cleanup")
        await _event_loop_barrier_async()

        assert not caller.done()
        assert harness.cleanup_cancellations == 0
        harness.release_cleanup()
        with pytest.raises(asyncio.CancelledError, match="cancel during cleanup"):
            await asyncio.wait_for(caller, timeout=5)
        assert harness.cleanup_finished.is_set()


async def _check_token_cancellation_before_browser_coroutine_entry_is_acknowledged_async() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    await authenticator._ensure_browser_thread_started_async()
    browser_loop = authenticator._browser_loop
    thread = authenticator._browser_thread
    assert browser_loop is not None
    assert thread is not None
    main_loop = asyncio.get_running_loop()
    browser_blocked = asyncio.Event()
    release_browser = threading.Event()
    original_tasks = asyncio.all_tasks()

    def block_browser_dispatch() -> None:
        main_loop.call_soon_threadsafe(browser_blocked.set)
        release_browser.wait(timeout=5)

    callers: list[asyncio.Task[str]] = []
    try:
        with patch.object(authenticator, "_create_playwright_context_manager") as factory:
            browser_loop.call_soon_threadsafe(block_browser_dispatch)
            await asyncio.wait_for(browser_blocked.wait(), timeout=5)
            caller = asyncio.create_task(authenticator.get_token_async())
            callers.append(caller)
            await _event_loop_barrier_async()
            caller.cancel("original pre-start cancellation")
            await _event_loop_barrier_async()
            caller.cancel("repeated pre-start cancellation")
            await _event_loop_barrier_async()
            assert not caller.done()
            assert authenticator._token_fetch_lock.locked()
            release_browser.set()
            with pytest.raises(asyncio.CancelledError, match="original pre-start cancellation"):
                await asyncio.wait_for(caller, timeout=5)
            factory.assert_not_called()
            assert not authenticator._token_fetch_lock.locked()
    finally:
        release_browser.set()
        await asyncio.gather(*callers, return_exceptions=True)
        await authenticator.close_async()
    assert not thread.is_alive()
    assert authenticator._browser_loop is None
    assert not (asyncio.all_tasks() - original_tasks)


async def _check_close_cancellation_waits_for_thread_stop_async() -> None:
    async with _browser_lifetime_async(phase="close") as harness:
        authenticator = harness.authenticator
        await authenticator.get_token_async()
        harness.release_cleanup()
        stopping = asyncio.Event()
        release_stop = asyncio.Event()
        original_stop = authenticator._stop_browser_thread_async

        async def delayed_stop_async() -> None:
            stopping.set()
            await release_stop.wait()
            await original_stop()

        with patch.object(authenticator, "_stop_browser_thread_async", side_effect=delayed_stop_async):
            closer = asyncio.create_task(authenticator.close_async())
            harness.tasks.append(closer)
            try:
                await asyncio.wait_for(stopping.wait(), timeout=5)
                closer.cancel("original stop cancellation")
                await _event_loop_barrier_async()
                closer.cancel("repeated stop cancellation")
                await _event_loop_barrier_async()
                assert not closer.done()
                assert authenticator._browser_thread is not None
                assert authenticator._browser_thread.is_alive()
            finally:
                release_stop.set()
            with pytest.raises(asyncio.CancelledError, match="original stop cancellation"):
                await asyncio.wait_for(closer, timeout=5)
        assert authenticator._browser_thread is None


async def _check_token_cancellation_waits_for_browser_startup_async() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    main_loop = asyncio.get_running_loop()
    starting = asyncio.Event()
    release_start = threading.Event()
    original_start = authenticator._run_browser_event_loop

    def delayed_start() -> None:
        main_loop.call_soon_threadsafe(starting.set)
        release_start.wait(timeout=5)
        original_start()

    with (
        patch.object(authenticator, "_run_browser_event_loop", side_effect=delayed_start),
        patch.object(authenticator, "_create_playwright_context_manager") as factory,
    ):
        caller = asyncio.create_task(authenticator.get_token_async())
        try:
            await asyncio.wait_for(starting.wait(), timeout=5)
            caller.cancel("original startup cancellation")
            await _event_loop_barrier_async()
            caller.cancel("repeated startup cancellation")
            await _event_loop_barrier_async()
            assert not caller.done()
            assert authenticator._token_fetch_lock.locked()
            release_start.set()
            with pytest.raises(asyncio.CancelledError, match="original startup cancellation"):
                await asyncio.wait_for(caller, timeout=5)
            factory.assert_not_called()
        finally:
            release_start.set()
            await asyncio.to_thread(authenticator._browser_loop_started.wait, 5)
            await asyncio.gather(caller, return_exceptions=True)
            thread = authenticator._browser_thread
            await authenticator.close_async()
        assert thread is not None
        assert not thread.is_alive()
        assert authenticator._browser_loop is None


async def _check_startup_cancellation_and_close_wait_until_browser_loop_runs_async() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    main_loop = asyncio.get_running_loop()
    original_tasks = asyncio.all_tasks()
    before_run = asyncio.Event()
    running = asyncio.Event()
    release_run = threading.Event()
    original_run = asyncio.BaseEventLoop.run_forever
    original_stop = authenticator._stop_browser_thread_async
    premature_stops: list[bool] = []
    callers: list[asyncio.Task[Any]] = []

    def delay_run_forever(loop: asyncio.BaseEventLoop) -> None:
        loop.call_soon(lambda: main_loop.call_soon_threadsafe(running.set))
        main_loop.call_soon_threadsafe(before_run.set)
        release_run.wait(timeout=5)
        original_run(loop)

    async def stop_safely_async() -> None:
        browser_loop = authenticator._browser_loop
        if browser_loop is not None:
            premature_stops.append(not browser_loop.is_running())
            # Keep the old failing implementation's teardown from hanging in join.
            release_run.set()
            await asyncio.wait_for(running.wait(), timeout=5)
        await original_stop()

    with (
        patch.object(asyncio.BaseEventLoop, "run_forever", autospec=True, side_effect=delay_run_forever),
        patch.object(authenticator, "_stop_browser_thread_async", side_effect=stop_safely_async),
        patch.object(authenticator, "_create_playwright_context_manager") as factory,
    ):
        caller = asyncio.create_task(authenticator.get_token_async())
        callers.append(caller)
        try:
            await asyncio.wait_for(before_run.wait(), timeout=5)
            thread = authenticator._browser_thread
            assert thread is not None
            caller.cancel("original readiness cancellation")
            closer = asyncio.create_task(authenticator.close_async())
            callers.append(closer)
            await _event_loop_barrier_async()

            assert not authenticator._browser_loop_started.is_set()
            assert not caller.done()
            assert not closer.done()
            assert authenticator._token_fetch_lock.locked()
            caller.cancel("repeated readiness cancellation")
            await _event_loop_barrier_async()
            assert not caller.done()
            assert not closer.done()
            assert premature_stops == []

            release_run.set()
            with pytest.raises(asyncio.CancelledError, match="original readiness cancellation"):
                await asyncio.wait_for(caller, timeout=5)
            await asyncio.wait_for(closer, timeout=5)
            factory.assert_not_called()
            assert premature_stops == [False]
            assert not thread.is_alive()
            assert authenticator._browser_loop is None
        finally:
            release_run.set()
            await asyncio.gather(*callers, return_exceptions=True)
            await authenticator.close_async()
    assert authenticator._browser_thread is None
    assert not (asyncio.all_tasks() - original_tasks)


async def _check_queued_close_cancellation_drains_capture_and_cleanup_async(cleanup_fails: bool) -> None:
    cleanup_error = RuntimeError("queued close cleanup failed") if cleanup_fails else None
    async with _browser_lifetime_async(phase="wait", cleanup_error=cleanup_error) as harness:
        authenticator = harness.authenticator
        caller = asyncio.create_task(authenticator.get_token_async())
        harness.tasks.append(caller)
        await asyncio.wait_for(harness.operation_started.wait(), timeout=5)
        browser_loop = authenticator._browser_loop
        thread = authenticator._browser_thread
        assert browser_loop is not None
        assert thread is not None
        closer = asyncio.create_task(authenticator.close_async())
        harness.tasks.append(closer)
        await _event_loop_barrier_async()
        closer.cancel("original queued close cancellation")
        await _event_loop_barrier_async()
        closer.cancel("repeated queued close cancellation")
        await _event_loop_barrier_async()

        assert not caller.done()
        assert not closer.done()
        assert authenticator._token_fetch_lock.locked()
        assert not harness.cleanup_started.is_set()
        assert not harness.stopped_before_cleanup
        token = _make_token()
        callback = harness.page.on.call_args.args[1]
        browser_loop.call_soon_threadsafe(
            callback, MagicMock(url=f"{authenticator.DEFAULT_WEBSOCKET_BASE_URL}/ChatHub?access_token={token}")
        )
        assert await asyncio.wait_for(caller, timeout=5) == token
        await asyncio.wait_for(harness.cleanup_started.wait(), timeout=5)
        assert not closer.done()
        assert authenticator._access_token is None
        assert await authenticator.get_claims_async() == {}
        assert harness.cleanup_cancellations == 0
        closer.cancel("cancel again during queued close cleanup")
        await _event_loop_barrier_async()
        assert not closer.done()
        harness.release_cleanup()

        with pytest.raises(asyncio.CancelledError, match="original queued close cancellation") as raised:
            await asyncio.wait_for(closer, timeout=5)
        assert raised.value.__cause__ is cleanup_error
        assert harness.cleanup_finished.is_set()
        assert not harness.stopped_before_cleanup
        assert not thread.is_alive()
        assert authenticator._browser_thread is None
        assert authenticator._browser_loop is None
        assert authenticator._access_token is None
        assert await authenticator.get_claims_async() == {}
        harness.context.close.assert_awaited_once()
        harness.manager.__aexit__.assert_awaited_once()
        harness.page.remove_listener.assert_called_once()


def _make_token(
    *,
    claims: dict[str, object] | None = None,
) -> str:
    """Create a JWT token with a short expiry for testing."""
    token_claims = (
        claims
        if claims is not None
        else {
            "tid": "tenant_id",
            "oid": "object_id",
            "exp": int(time.time()) + 3600,
        }
    )
    return jwt.encode(
        token_claims,
        key=_TEST_JWT_KEY,
        algorithm="HS256",
    )


def test_init_uses_custom_profile_path(tmp_path: Path) -> None:

    profile_path = tmp_path / "copilot_profile"

    authenticator = BrowserSessionCopilotAuthenticator(
        profile_path=profile_path,
    )

    assert authenticator.profile_path == profile_path


def test_init_uses_default_profile_path() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    assert authenticator.profile_path == (CONFIGURATION_DIRECTORY_PATH / "copilot_browser_profiles" / "default")


@pytest.mark.parametrize("timeout", [0, -1])
def test_init_rejects_non_positive_capture_timeout(timeout: int) -> None:
    with pytest.raises(ValueError, match="token_capture_timeout_seconds must be a positive integer."):
        BrowserSessionCopilotAuthenticator(
            token_capture_timeout_seconds=timeout,
        )


@pytest.mark.parametrize("expiry_buffer", [0, -1])
def test_init_rejects_non_positive_expiry_buffer(expiry_buffer: int) -> None:
    with pytest.raises(ValueError, match="expiry_buffer_seconds must be a positive integer."):
        BrowserSessionCopilotAuthenticator(
            expiry_buffer_seconds=expiry_buffer,
        )


async def test_get_token_async_captures_token_when_missing() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    captured_token = _make_token()

    with patch.object(
        BrowserSessionCopilotAuthenticator,
        "_capture_access_token_async",
        new=AsyncMock(return_value=captured_token),
        create=True,
    ) as capture:
        result = await authenticator.get_token_async()

    assert result == captured_token
    capture.assert_awaited_once()


async def test_get_claims_async_returns_captured_token_claims() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    token = _make_token()

    with patch.object(
        BrowserSessionCopilotAuthenticator,
        "_capture_access_token_async",
        new=AsyncMock(return_value=token),
    ):
        await authenticator.get_token_async()

    claims = await authenticator.get_claims_async()

    assert claims["tid"] == "tenant_id"
    assert claims["oid"] == "object_id"


@pytest.mark.parametrize("missing_claim", ["tid", "oid", "exp"])
async def test_get_token_async_rejects_missing_required_claims(missing_claim: str) -> None:
    claims = {
        "tid": "tenant_id",
        "oid": "object_id",
        "exp": int(time.time()) + 3600,
    }
    claims.pop(missing_claim)

    token = _make_token(claims=claims)
    authenticator = BrowserSessionCopilotAuthenticator()

    with patch.object(
        BrowserSessionCopilotAuthenticator,
        "_capture_access_token_async",
        new=AsyncMock(return_value=token),
    ):
        with pytest.raises(ValueError, match=f"Missing required claim: {missing_claim}"):
            await authenticator.get_token_async()


async def test_get_token_async_recaptures_token_within_expiry_buffer() -> None:
    authenticator = BrowserSessionCopilotAuthenticator(
        expiry_buffer_seconds=300,
    )
    initial_token = _make_token(
        claims={
            "tid": "tenant_id",
            "oid": "object_id",
            "exp": 1000,
        }
    )
    refreshed_token = _make_token(
        claims={
            "tid": "tenant_id",
            "oid": "object_id",
            "exp": 2000,
        }
    )

    capture = AsyncMock(side_effect=[initial_token, refreshed_token])

    with (
        patch.object(
            BrowserSessionCopilotAuthenticator,
            "_capture_access_token_async",
            new=capture,
        ),
        patch(
            "pyrit.auth.browser_session_copilot_authenticator.time.time",
            return_value=0,
        ) as current_time,
    ):
        assert await authenticator.get_token_async() == initial_token

        current_time.return_value = 750

        assert await authenticator.get_token_async() == refreshed_token

    assert capture.await_count == 2


async def test_get_token_async_serializes_concurrent_capture() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    token = _make_token()
    capture_started = asyncio.Event()
    release_capture = asyncio.Event()

    async def capture_token_async() -> str:
        capture_started.set()
        await release_capture.wait()
        return token

    with patch.object(
        BrowserSessionCopilotAuthenticator,
        "_capture_access_token_async",
        side_effect=capture_token_async,
    ) as capture:
        first_request = asyncio.create_task(authenticator.get_token_async())
        await capture_started.wait()

        second_request = asyncio.create_task(authenticator.get_token_async())
        release_capture.set()

        results = await asyncio.gather(first_request, second_request)

    assert results == [token, token]
    capture.assert_awaited_once()


async def test_get_token_async_captures_independently_per_instance(
    tmp_path: Path,
) -> None:
    first = BrowserSessionCopilotAuthenticator(profile_path=tmp_path / "persona_one")
    second = BrowserSessionCopilotAuthenticator(profile_path=tmp_path / "persona_two")

    first_token = _make_token(
        claims={
            "tid": "tenant_id",
            "oid": "object_id_one",
            "exp": int(time.time()) + 3600,
        }
    )
    second_token = _make_token(
        claims={
            "tid": "tenant_id",
            "oid": "object_id_two",
            "exp": int(time.time()) + 3600,
        }
    )

    with patch.object(
        BrowserSessionCopilotAuthenticator,
        "_capture_access_token_async",
        new=AsyncMock(side_effect=[first_token, second_token]),
    ) as capture:
        results = await asyncio.gather(
            first.get_token_async(),
            second.get_token_async(),
        )

    assert results == [first_token, second_token]
    assert capture.await_count == 2


async def test_refresh_token_async_forces_new_capture() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    initial_token = _make_token()
    refreshed_token = _make_token(
        claims={
            "tid": "tenant_id",
            "oid": "object_id",
            "exp": int(time.time()) + 7200,
        }
    )
    capture = AsyncMock(side_effect=[initial_token, refreshed_token])

    with patch.object(
        BrowserSessionCopilotAuthenticator,
        "_capture_access_token_async",
        new=capture,
    ):
        assert await authenticator.get_token_async() == initial_token
        assert await authenticator.refresh_token_async() == refreshed_token

    assert capture.await_count == 2


@pytest.mark.parametrize(
    "websocket_path",
    [
        "m365Copilot/Chathub",
        "m365Copilot/ChatHub",
        "m365Copilot/StreamHub",
        "m365Copilot/Streamhub",
        "m365copilot/chathub",
        "M365COPILOT/STREAMHUB",
        "m365CoPiLoT/sTrEaMhUb",
    ],
)
def test_extract_access_token_from_copilot_websocket_url(websocket_path: str) -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    websocket_url = (
        f"wss://substrate.svc.cloud.microsoft/{websocket_path}/user@tenant"
        "?access_token=MiXeD%2BToken%2FValue%3D&source=OfficeWeb"
    )

    result = authenticator._extract_access_token_from_websocket_url(websocket_url=websocket_url)

    assert result == "MiXeD+Token/Value="


@pytest.mark.parametrize(
    "websocket_url",
    [
        "ws://substrate.svc.cloud.microsoft/m365Copilot/Chathub/user@tenant?access_token=test-token",
        "https://substrate.svc.cloud.microsoft/m365Copilot/StreamHub/user@tenant?access_token=test-token",
        "wss://evil.example/m365Copilot/Chathub/user@tenant?access_token=test-token",
        "wss://substrate.svc.cloud.microsoft.evil.example/m365Copilot/StreamHub/user@tenant?access_token=test-token",
        "wss://not-substrate.svc.cloud.microsoft/m365Copilot/StreamHub/user@tenant?access_token=test-token",
        "wss://substrate.svc.cloud.microsoft/other/path?access_token=test-token",
        "wss://substrate.svc.cloud.microsoft/m365CopilotOther/StreamHub/user@tenant?access_token=test-token",
        "wss://substrate.svc.cloud.microsoft/M365COPILOTOther/StreamHub/user@tenant?access_token=test-token",
        "wss://substrate.svc.cloud.microsoft/other/m365Copilot/StreamHub/user@tenant?access_token=test-token",
        "wss://substrate.svc.cloud.microsoft/m365Copilot?access_token=test-token",
        "wss://substrate.svc.cloud.microsoft/m365Copilot/Chathub/user@tenant",
        "wss://substrate.svc.cloud.microsoft/m365Copilot/StreamHub/user@tenant?access_token=",
        "wss://substrate.svc.cloud.microsoft/m365Copilot/StreamHub/user@tenant?ACCESS_TOKEN=test-token",
    ],
)
def test_extract_access_token_rejects_unexpected_url(
    websocket_url: str,
) -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    result = authenticator._extract_access_token_from_websocket_url(websocket_url=websocket_url)
    assert result is None


@pytest.mark.parametrize("hub", ["Chathub", "StreamHub", "sTrEaMhUb"])
async def test_handle_websocket_url_resolves_token_future(hub: str) -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    token_future = asyncio.get_running_loop().create_future()
    websocket_url = f"wss://substrate.svc.cloud.microsoft/m365Copilot/{hub}/user@tenant?access_token=MiXeD-Token"

    result = authenticator._handle_websocket_url(
        websocket_url=websocket_url,
        token_future=token_future,
    )

    assert await asyncio.wait_for(token_future, timeout=1) == "MiXeD-Token"


async def test_wait_for_token_async_raises_clear_timeout() -> None:
    authenticator = BrowserSessionCopilotAuthenticator(
        token_capture_timeout_seconds=1,
    )
    token_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    with pytest.raises(
        TimeoutError,
        match="Timed out waiting for access token capture.",
    ):
        await authenticator._wait_for_token_async(token_future=token_future)


def test_init_uses_browser_capture_defaults() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    assert authenticator._browser_channel == "msedge"
    assert authenticator._headless is False
    assert authenticator._copilot_url == "https://m365.cloud.microsoft/chat"


async def test_capture_access_token_async_keeps_context_until_closed(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "copilot-profile"
    authenticator = BrowserSessionCopilotAuthenticator(
        profile_path=profile_path,
    )

    page = MagicMock()
    page.goto = AsyncMock()

    browser_context = MagicMock()
    browser_context.pages = []
    browser_context.new_page = AsyncMock(return_value=page)
    browser_context.close = AsyncMock()

    playwright = MagicMock()
    playwright.chromium.launch_persistent_context = AsyncMock(return_value=browser_context)

    playwright_manager = AsyncMock()
    playwright_manager.__aenter__.return_value = playwright
    playwright_manager.__aexit__.return_value = None

    with (
        patch.object(
            authenticator,
            "_create_playwright_context_manager",
            return_value=playwright_manager,
            create=True,
        ),
        patch.object(
            authenticator,
            "_wait_for_token_async",
            new=AsyncMock(return_value="test-token"),
        ),
        patch.object(
            authenticator,
            "_minimize_browser_window_async",
            new=AsyncMock(),
        ) as minimize,
    ):
        result = await authenticator._capture_access_token_async()

    assert result == "test-token"
    playwright.chromium.launch_persistent_context.assert_awaited_once_with(
        user_data_dir=str(profile_path),
        channel="msedge",
        headless=False,
    )
    browser_context.new_page.assert_awaited_once()
    page.on.assert_called_once()
    page.goto.assert_awaited_once_with("https://m365.cloud.microsoft/chat")
    minimize.assert_awaited_once_with(page=page)
    browser_context.close.assert_not_awaited()
    playwright_manager.__aexit__.assert_not_awaited()

    await authenticator.close_async()

    browser_context.close.assert_awaited_once()
    playwright_manager.__aexit__.assert_awaited_once()


async def test_capture_access_token_async_closes_context_on_navigation_error(
    tmp_path: Path,
) -> None:
    authenticator = BrowserSessionCopilotAuthenticator(
        profile_path=tmp_path / "copilot-profile",
    )

    page = MagicMock()
    page.goto = AsyncMock(side_effect=RuntimeError("navigation failed"))

    browser_context = MagicMock()
    browser_context.new_page = AsyncMock(return_value=page)
    browser_context.close = AsyncMock()
    browser_context.pages = []

    playwright = MagicMock()
    playwright.chromium.launch_persistent_context = AsyncMock(return_value=browser_context)

    playwright_manager = AsyncMock()
    playwright_manager.__aenter__.return_value = playwright
    playwright_manager.__aexit__.return_value = None

    with (
        patch.object(
            authenticator,
            "_create_playwright_context_manager",
            return_value=playwright_manager,
        ),
        pytest.raises(RuntimeError, match="navigation failed"),
    ):
        await authenticator._capture_access_token_async()

    browser_context.close.assert_awaited_once()


def test_auth_package_exports_browser_session_authenticator() -> None:
    from pyrit.auth import BrowserSessionCopilotAuthenticator as ExportedAuthenticator

    assert ExportedAuthenticator is BrowserSessionCopilotAuthenticator


async def test_capture_access_token_async_reuses_existing_page() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    page = MagicMock()
    page.on = MagicMock()
    page.remove_listener = MagicMock()
    page.goto = AsyncMock()

    authenticator._page = page

    with (
        patch.object(
            authenticator,
            "_wait_for_token_async",
            new=AsyncMock(return_value="refreshed_token"),
        ),
        patch.object(
            authenticator,
            "_minimize_browser_window_async",
            new=AsyncMock(),
        ) as minimize,
    ):
        result = await authenticator._capture_access_token_async()

    assert result == "refreshed_token"
    page.goto.assert_awaited_once_with("https://m365.cloud.microsoft/chat")
    page.on.assert_called_once()
    page.remove_listener.assert_called_once()
    minimize.assert_awaited_once_with(page=page)


async def test_minimize_browser_window_async() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    cdp_session = MagicMock()
    cdp_session.send = AsyncMock(
        side_effect=[
            {"windowId": 42},
            {},
        ]
    )
    cdp_session.detach = AsyncMock()

    page = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=cdp_session)

    await authenticator._minimize_browser_window_async(page=page)

    assert cdp_session.send.await_args_list == [
        call("Browser.getWindowForTarget"),
        call("Browser.setWindowBounds", {"windowId": 42, "bounds": {"windowState": "minimized"}}),
    ]
    cdp_session.detach.assert_awaited_once()


async def test_close_async_is_idempotent() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    resources = AsyncMock()
    authenticator._browser_resources = resources

    await authenticator.close_async()
    await authenticator.close_async()

    resources.aclose.assert_awaited_once()
    assert authenticator._browser_resources is None
    assert authenticator._browser_context is None
    assert authenticator._page is None


async def test_async_context_manager_returns_authenticator() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    async with authenticator as entered:
        assert entered is authenticator


async def test_async_context_manager_closes_on_exit() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    with patch.object(
        authenticator,
        "close_async",
        new=AsyncMock(),
    ) as close:
        async with authenticator:
            pass

    close.assert_awaited_once()


async def test_close_async_clears_token_state() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    token = _make_token()

    with patch.object(
        authenticator,
        "_capture_access_token_async",
        new=AsyncMock(return_value=token),
    ):
        await authenticator.get_token_async()

    await authenticator.close_async()

    assert authenticator._access_token is None
    assert await authenticator.get_claims_async() == {}


async def test_capture_access_token_async_always_delegates_to_browser_thread() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    with (
        patch.object(
            authenticator,
            "_ensure_browser_session_async",
            new=AsyncMock(side_effect=AssertionError("Browser must not launch in this unit test")),
        ),
        patch.object(
            authenticator,
            "_run_on_browser_thread_async",
            new=AsyncMock(return_value="test-token"),
            create=True,
        ) as run_on_thread,
    ):
        result = await authenticator._capture_access_token_async()

    assert result == "test-token"
    run_on_thread.assert_awaited_once()


async def test_run_on_browser_thread_async_reuses_thread_and_loop() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    async def identify_execution_context_async() -> tuple[int, int]:
        return threading.get_ident(), id(asyncio.get_running_loop())

    try:
        first_context = await authenticator._run_on_browser_thread_async(
            operation=identify_execution_context_async,
        )
        second_context = await authenticator._run_on_browser_thread_async(
            operation=identify_execution_context_async,
        )
    finally:
        await authenticator.close_async()

    assert first_context == second_context
    assert first_context[0] != threading.get_ident()


@pytest.mark.parametrize("outcome", ["result", "cancelled", "error"])
@pytest.mark.parametrize("request_cancellation", [False, True])
async def test_await_completion_async_drains_repeated_cancellation(*, outcome: str, request_cancellation: bool) -> None:
    completion: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    cancel = MagicMock()
    cleanup_error = RuntimeError("cleanup failed")
    caller = asyncio.create_task(
        BrowserSessionCopilotAuthenticator._await_completion_async(
            completion=completion,
            cancel=cancel if request_cancellation else None,
        )
    )
    try:
        await asyncio.sleep(0)
        for message in ("original cancellation", "repeated cancellation"):
            caller.cancel(message)
            await asyncio.sleep(0)
            assert not caller.done()
            assert not completion.done()

        if outcome == "result":
            completion.set_result("finished")
        elif outcome == "cancelled":
            completion.cancel()
        else:
            completion.set_exception(cleanup_error)

        with pytest.raises(asyncio.CancelledError, match="original cancellation") as error:
            await caller
        assert error.value.__cause__ is (cleanup_error if outcome == "error" else None)
        if request_cancellation:
            cancel.assert_called_once()
        else:
            cancel.assert_not_called()
    finally:
        if not completion.done():
            completion.set_result("released")
        await asyncio.gather(caller, return_exceptions=True)


# A missing cross-thread completion acknowledgement cannot be bounded by asyncio cancellation.
@pytest.mark.timeout(30, method="thread")
async def test_run_on_browser_thread_async_drains_cancelled_operation() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    caller_loop = asyncio.get_running_loop()
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = threading.Event()

    async def operation_async() -> str:
        caller_loop.call_soon_threadsafe(started.set)
        try:
            await asyncio.Future()
            return "unused"
        finally:
            caller_loop.call_soon_threadsafe(cleaning.set)
            await release_cleanup.wait()
            cleanup_finished.set()

    caller = asyncio.create_task(authenticator._run_on_browser_thread_async(operation=operation_async))
    try:
        await started.wait()
        caller.cancel("original cancellation")
        await cleaning.wait()
        caller.cancel("repeated cancellation")
        await asyncio.sleep(0)
        assert not caller.done()
        assert not cleanup_finished.is_set()

        browser_loop = authenticator._browser_loop
        assert browser_loop is not None
        browser_loop.call_soon_threadsafe(release_cleanup.set)
        with pytest.raises(asyncio.CancelledError, match="original cancellation"):
            await caller
        assert cleanup_finished.is_set()
    finally:
        browser_loop = authenticator._browser_loop
        if browser_loop is not None:
            browser_loop.call_soon_threadsafe(release_cleanup.set)
        await asyncio.gather(caller, return_exceptions=True)
        await authenticator.close_async()

    assert authenticator._browser_loop is None
    assert authenticator._browser_thread is None


@pytest.mark.timeout(30, method="thread")
async def test_run_on_browser_thread_async_closes_coroutine_when_scheduling_fails() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    operation = AsyncMock()
    scheduling_error = RuntimeError("task scheduling failed")
    try:
        await authenticator._ensure_browser_thread_started_async()
        browser_loop = authenticator._browser_loop
        assert browser_loop is not None
        with patch.object(browser_loop, "create_task", side_effect=scheduling_error) as create_task:
            with pytest.raises(RuntimeError, match="task scheduling failed") as error:
                await authenticator._run_on_browser_thread_async(operation=operation)

        assert error.value is scheduling_error
        create_task.assert_called_once()
        assert create_task.call_args.args[0].cr_frame is None
        operation.assert_not_awaited()
    finally:
        await authenticator.close_async()


async def test_close_async_closes_resources_on_browser_thread() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    resources = MagicMock()
    cleanup_thread_id: int | None = None

    async def close_resources_async() -> None:
        nonlocal cleanup_thread_id
        cleanup_thread_id = threading.get_ident()

    resources.aclose = AsyncMock(side_effect=close_resources_async)

    async def install_resources_async() -> int:
        authenticator._browser_resources = resources
        return threading.get_ident()

    owner_thread_id = await authenticator._run_on_browser_thread_async(
        operation=install_resources_async,
    )

    await authenticator.close_async()

    assert cleanup_thread_id == owner_thread_id


@pytest.mark.parametrize(
    ("websocket_base_url", "websocket_prefix"),
    [
        (
            "wss://substrate.svc.cloud.microsoft/m365Copilot/ChatHub",
            "wss://substrate.svc.cloud.microsoft/M365COPILOT/chathub",
        ),
        (
            "wss://substrate.svc.cloud.microsoft/M365COPILOT/sTrEaMhUb",
            "wss://substrate.svc.cloud.microsoft/m365Copilot/StreamHub",
        ),
        ("wss://copilot.example/CuStOm/HuB", "wss://copilot.example/custom/hub"),
    ],
)
def test_extract_access_token_uses_custom_prefix(
    *,
    websocket_base_url: str,
    websocket_prefix: str,
) -> None:
    authenticator = BrowserSessionCopilotAuthenticator(websocket_base_url=websocket_base_url)

    assert (
        authenticator._extract_access_token_from_websocket_url(
            websocket_url=f"{websocket_prefix}/user@tenant?access_token=MiXeD-Token",
        )
        == "MiXeD-Token"
    )
    assert (
        authenticator._extract_access_token_from_websocket_url(
            websocket_url=f"{websocket_prefix}Other/user@tenant?access_token=MiXeD-Token",
        )
        is None
    )
    assert (
        authenticator._extract_access_token_from_websocket_url(
            websocket_url="wss://substrate.svc.cloud.microsoft/m365Copilot/OtherHub/user@tenant?access_token=MiXeD-Token",
        )
        is None
    )


@pytest.mark.parametrize("base_path", ["m365Copilot/", "m365Copilot/Chathub/", "M365COPILOT/CHATHUB///"])
def test_extract_access_token_accepts_trailing_slash_in_base_url(base_path: str) -> None:
    authenticator = BrowserSessionCopilotAuthenticator(
        websocket_base_url=f"wss://substrate.svc.cloud.microsoft/{base_path}",
    )

    result = authenticator._extract_access_token_from_websocket_url(
        websocket_url=("wss://substrate.svc.cloud.microsoft/m365Copilot/Chathub/user@tenant?access_token=test-token"),
    )

    assert result == "test-token"


async def test_get_token_async_rejects_new_token_within_expiry_buffer() -> None:
    authenticator = BrowserSessionCopilotAuthenticator(
        expiry_buffer_seconds=300,
    )
    token = _make_token(
        claims={
            "tid": "tenant_id",
            "oid": "object_id",
            "exp": 1200,
        }
    )

    with (
        patch.object(
            authenticator,
            "_capture_access_token_async",
            new=AsyncMock(return_value=token),
        ),
        patch(
            "pyrit.auth.browser_session_copilot_authenticator.time.time",
            return_value=1000,
        ),
        pytest.raises(ValueError, match="within the expiry buffer"),
    ):
        await authenticator.get_token_async()

    assert authenticator._access_token is None
    assert await authenticator.get_claims_async() == {}


@pytest.mark.parametrize(
    ("claim", "value", "message"),
    [
        ("tid", "", "invalid tid claim"),
        ("oid", "   ", "invalid oid claim"),
        ("exp", True, "invalid exp claim"),
        ("exp", "tomorrow", "invalid exp claim"),
    ],
)
async def test_get_token_async_rejects_invalid_required_claim(
    claim: str,
    value: object,
    message: str,
) -> None:
    claims: dict[str, object] = {
        "tid": "tenant_id",
        "oid": "object_id",
        "exp": int(time.time()) + 3600,
    }
    claims[claim] = value
    token = _make_token(claims=claims)
    authenticator = BrowserSessionCopilotAuthenticator()

    with (
        patch.object(
            authenticator,
            "_capture_access_token_async",
            new=AsyncMock(return_value=token),
        ),
        pytest.raises(ValueError, match=message),
    ):
        await authenticator.get_token_async()

    assert authenticator._access_token is None


async def test_close_async_waits_for_active_token_operation() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    await authenticator._token_fetch_lock.acquire()
    close_task = asyncio.create_task(authenticator.close_async())

    await asyncio.sleep(0)

    assert not close_task.done()

    authenticator._token_fetch_lock.release()
    await close_task


async def test_ensure_browser_session_async_cleans_up_on_cancellation() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    playwright = MagicMock()
    playwright.chromium.launch_persistent_context = AsyncMock(
        side_effect=asyncio.CancelledError,
    )

    playwright_manager = AsyncMock()
    playwright_manager.__aenter__.return_value = playwright
    playwright_manager.__aexit__.return_value = None

    with (
        patch.object(
            authenticator,
            "_create_playwright_context_manager",
            return_value=playwright_manager,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await authenticator._ensure_browser_session_async()

    playwright_manager.__aexit__.assert_awaited_once()


async def test_capture_on_browser_loop_cleans_up_on_cancellation() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    page = MagicMock()
    page.on = MagicMock()
    page.remove_listener = MagicMock()
    page.goto = AsyncMock(side_effect=asyncio.CancelledError)

    with (
        patch.object(
            authenticator,
            "_ensure_browser_session_async",
            new=AsyncMock(return_value=page),
        ),
        patch.object(
            authenticator,
            "_close_browser_resources_async",
            new=AsyncMock(),
        ) as close_resources,
        pytest.raises(asyncio.CancelledError),
    ):
        await authenticator._capture_access_token_on_browser_loop_async()

    close_resources.assert_awaited_once()
    page.remove_listener.assert_called_once()


@pytest.mark.parametrize(
    "websocket_base_url",
    [
        "ws://substrate.svc.cloud.microsoft/m365Copilot/Chathub",
        "https://substrate.svc.cloud.microsoft/m365Copilot/Chathub",
        "wss:///m365Copilot/Chathub",
        "wss://substrate.svc.cloud.microsoft",
    ],
)
def test_init_rejects_invalid_websocket_base_url(
    websocket_base_url: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="websocket_base_url must be a valid wss URL with a path",
    ):
        BrowserSessionCopilotAuthenticator(
            websocket_base_url=websocket_base_url,
        )
