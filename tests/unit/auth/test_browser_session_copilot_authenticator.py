# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import jwt
import pytest

from pyrit.auth import (
    BrowserSessionCopilotAuthenticator,
)
from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

_TEST_JWT_KEY = "a" * 32


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


def test_extract_access_token_from_chathub_url() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    websocket_url = "wss://substrate.svc.cloud.microsoft/m365Copilot/Chathub/user@tenant?access_token=test-token"

    result = authenticator._extract_access_token_from_websocket_url(websocket_url=websocket_url)

    assert result == "test-token"


@pytest.mark.parametrize(
    "websocket_url",
    [
        "ws://substrate.svc.cloud.microsoft/m365Copilot/Chathub/user@tenant?access_token=test-token",
        "wss://evil.example/m365Copilot/Chathub/user@tenant?access_token=test-token",
        "wss://substrate.svc.cloud.microsoft/other/path?access_token=test-token",
        "wss://substrate.svc.cloud.microsoft/m365Copilot/Chathub/user@tenant",
    ],
)
def test_extract_access_token_rejects_unexpected_url(
    websocket_url: str,
) -> None:
    authenticator = BrowserSessionCopilotAuthenticator()

    result = authenticator._extract_access_token_from_websocket_url(websocket_url=websocket_url)
    assert result is None


async def test_handle_websocket_url_resolves_token_future() -> None:
    authenticator = BrowserSessionCopilotAuthenticator()
    token_future = asyncio.get_running_loop().create_future()
    websocket_url = "wss://substrate.svc.cloud.microsoft/m365Copilot/Chathub/user@tenant?access_token=test-token"

    result = authenticator._handle_websocket_url(
        websocket_url=websocket_url,
        token_future=token_future,
    )

    assert await token_future == "test-token"


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


def test_extract_access_token_accepts_trailing_slash_in_base_url() -> None:
    authenticator = BrowserSessionCopilotAuthenticator(
        websocket_base_url=("wss://substrate.svc.cloud.microsoft/m365Copilot/Chathub/"),
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
