# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from contextlib import AsyncExitStack
from pathlib import Path
from types import TracebackType
from typing import Any, TypeVar
from urllib.parse import parse_qs, urlparse

import jwt
from typing_extensions import Self

from pyrit.auth.authenticator import Authenticator
from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH

T = TypeVar("T")


class BrowserSessionCopilotAuthenticator(Authenticator):
    """Acquire Microsoft Copilot access token from a browser session."""

    DEFAULT_TOKEN_CAPTURE_TIMEOUT_SECONDS = 60
    DEFAULT_EXPIRY_BUFFER_SECONDS = 300
    DEFAULT_WEBSOCKET_BASE_URL = "wss://substrate.svc.cloud.microsoft/m365Copilot/Chathub"
    DEFAULT_BROWSER_CHANNEL = "msedge"
    DEFAULT_COPILOT_URL = "https://m365.cloud.microsoft/chat"

    def __init__(
        self,
        *,
        profile_path: Path | None = None,
        token_capture_timeout_seconds: int = DEFAULT_TOKEN_CAPTURE_TIMEOUT_SECONDS,
        expiry_buffer_seconds: int = DEFAULT_EXPIRY_BUFFER_SECONDS,
        websocket_base_url: str = DEFAULT_WEBSOCKET_BASE_URL,
        browser_channel: str = DEFAULT_BROWSER_CHANNEL,
        headless: bool = False,
        copilot_url: str = DEFAULT_COPILOT_URL,
    ) -> None:
        """
        Initialize the authenticator with a persistent browser profile.

        Args:
            profile_path (Path | None): Path to the persistent browser profile. If None, a default path is used.
            token_capture_timeout_seconds (int): Timeout in seconds for capturing the token. Must be a positive integer.
            expiry_buffer_seconds (int): Buffer time in seconds before token expiry. Must be a positive integer.
            websocket_base_url (str): Base URL for the Copilot Chathub websocket connection. Must be a valid wss URL.
            browser_channel (str): Browser channel to use for the session. Defaults to "msedge".
            headless (bool): Whether to run the browser in headless mode. Defaults to False.
            copilot_url (str): URL for the Copilot chat interface. Must be a valid HTTPS URL.

        Raises:
            ValueError: If token_capture_timeout_seconds or expiry_buffer_seconds is not a positive integer.
            ValueError: If websocket_base_url is not a valid wss URL with a path.
        """
        self._access_token: str | None = None
        self._token_fetch_lock = asyncio.Lock()
        self._claims: dict[str, Any] = {}
        if token_capture_timeout_seconds <= 0:
            raise ValueError("token_capture_timeout_seconds must be a positive integer.")
        if expiry_buffer_seconds <= 0:
            raise ValueError("expiry_buffer_seconds must be a positive integer.")

        normalized_websocket_base_url = websocket_base_url.rstrip("/")
        parsed_websocket_base_url = urlparse(normalized_websocket_base_url)

        if (
            parsed_websocket_base_url.scheme != "wss"
            or not parsed_websocket_base_url.hostname
            or not parsed_websocket_base_url.path.strip("/")
        ):
            raise ValueError("websocket_base_url must be a valid wss URL with a path.")

        self._profile_path = profile_path or (CONFIGURATION_DIRECTORY_PATH / "copilot_browser_profiles" / "default")
        self._token_capture_timeout_seconds = token_capture_timeout_seconds
        self._expiry_buffer_seconds = expiry_buffer_seconds
        self._websocket_base_url = normalized_websocket_base_url
        self._browser_channel = browser_channel
        self._headless = headless
        self._copilot_url = copilot_url
        self._browser_resources: AsyncExitStack | None = None
        self._browser_context: Any | None = None
        self._page: Any | None = None
        self._browser_loop: asyncio.AbstractEventLoop | None = None
        self._browser_thread: threading.Thread | None = None
        self._browser_loop_started = threading.Event()
        self._browser_loop_start_error: Exception | None = None

    @property
    def profile_path(self) -> Path:
        """Persistent browser profile path."""
        return self._profile_path

    async def __aenter__(self) -> Self:
        """
        Enter the asynchronous context manager.

        Returns:
            The authenticator instance itself.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the asynchronous context manager, closing the browser context and releasing resources."""
        await self.close_async()

    async def get_token_async(self) -> str:
        """
        Return the current token, capturing a new one if necessary.

        Returns:
            The current access token as a string.

        Raises:
            ValueError: If captured token claims are missing, invalid, or within the expiry buffer.
        """
        current_token = self._access_token
        if current_token is not None and self._has_fresh_token():
            return current_token

        async with self._token_fetch_lock:
            current_token = self._access_token
            if current_token is not None and self._has_fresh_token():
                return current_token

            return await self._capture_and_store_token_async()

    async def refresh_token_async(self) -> str:
        """
        Force a refresh of the current token, capturing a new one.

        Returns:
            The newly captured access token as a string.
        """
        async with self._token_fetch_lock:
            return await self._capture_and_store_token_async()

    async def close_async(self) -> None:
        """Close browser resources and discard authentication state."""
        async with self._token_fetch_lock:
            self._access_token = None
            self._claims = {}

            browser_loop = self._browser_loop

            try:
                if browser_loop is not None and browser_loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self._close_browser_resources_async(),
                        browser_loop,
                    )
                    await asyncio.wrap_future(future)
                else:
                    await self._close_browser_resources_async()
            finally:
                await self._stop_browser_thread_async()

    async def get_claims_async(self) -> dict[str, Any]:
        """Return the claims extracted from the current token."""
        return dict(self._claims)

    def _has_fresh_token(self) -> bool:
        """Return whether the current token is outside of the expiry buffer."""
        if self._access_token is None:
            return False

        expires_at: object = self._claims.get("exp")
        if not isinstance(expires_at, (int, float)):
            return False

        return expires_at - time.time() > self._expiry_buffer_seconds

    async def _capture_access_token_async(self) -> str:
        """
        Capture a token on the authenticator-owned browser event loop.

        Returns:
            The captured access token as a string.
        """
        return await self._run_on_browser_thread_async(
            operation=self._capture_access_token_on_browser_loop_async,
        )

    async def _run_on_browser_thread_async(
        self,
        *,
        operation: Callable[[], Coroutine[Any, Any, T]],
    ) -> T:
        """
        Run a browser operation on the retained browser event loop.

        Args:
            operation: An asynchronous callable representing the browser operation to run.

        Returns:
            The result of the browser operation.

        Raises:
            RuntimeError: If the browser event loop failed to start.
        """
        await self._ensure_browser_thread_started_async()

        browser_loop = self._browser_loop
        if browser_loop is None:
            raise RuntimeError("Browser event loop failed to start.")

        future = asyncio.run_coroutine_threadsafe(operation(), browser_loop)
        return await asyncio.wrap_future(future)

    async def _ensure_browser_thread_started_async(self) -> None:
        """
        Start the retained browser event-loop thread when needed.

        Raises:
            RuntimeError: If the browser event loop failed to start.
        """
        thread = self._browser_thread
        if thread is not None and thread.is_alive():
            return

        self._browser_loop_started.clear()
        self._browser_loop_start_error = None
        thread = threading.Thread(
            target=self._run_browser_event_loop,
            name="pyrit-copilot-browser",
            daemon=True,
        )
        self._browser_thread = thread
        thread.start()

        await asyncio.to_thread(self._browser_loop_started.wait)

        if self._browser_loop_start_error is not None:
            raise RuntimeError("Browser event loop failed to start.") from self._browser_loop_start_error

    def _run_browser_event_loop(self) -> None:
        """Run the browser event loop on its dedicated thread."""
        try:
            loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._browser_loop = loop
            self._browser_loop_started.set()
            loop.run_forever()
            loop.close()
        except Exception as error:
            self._browser_loop_start_error = error
        finally:
            self._browser_loop_started.set()
            self._browser_loop = None

    async def _stop_browser_thread_async(self) -> None:
        """Stop and join the retained browser thread."""
        loop = self._browser_loop
        thread = self._browser_thread

        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join)

        self._browser_loop = None
        self._browser_thread = None

    async def _capture_access_token_on_browser_loop_async(self) -> str:
        """
        Capture a Copilot access token from a persistent Edge session.

        Returns:
            The captured access token as a string.
        """
        token_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        page = await self._ensure_browser_session_async()

        def handle_websocket(websocket: Any) -> None:
            self._handle_websocket_url(
                websocket_url=str(websocket.url),
                token_future=token_future,
            )

        page.on("websocket", handle_websocket)

        try:
            await page.goto(self._copilot_url)
            access_token = await self._wait_for_token_async(
                token_future=token_future,
            )

            if not self._headless:
                await self._minimize_browser_window_async(page=page)

            return access_token
        except BaseException:
            await self._close_browser_resources_async()
            raise
        finally:
            page.remove_listener("websocket", handle_websocket)

    async def _capture_and_store_token_async(self) -> str:
        """
        Capture, validate and store a new Copilot access token.

        Returns:
            The newly captured access token as a string.

        Raises:
            ValueError: If captured token claims are missing, invalid, or within the expiry buffer.
        """
        captured_token = await self._capture_access_token_async()
        claims = jwt.decode(
            captured_token,
            algorithms=["RS256"],
            options={"verify_signature": False},
        )

        required_claims = {"tid", "oid", "exp"}
        missing_claims = required_claims - claims.keys()
        if missing_claims:
            raise ValueError(f"Missing required claim: {', '.join(missing_claims)}")

        tenant_id = claims["tid"]
        object_id = claims["oid"]
        expires_at = claims["exp"]

        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("Captured Copilot token has an invalid tid claim.")

        if not isinstance(object_id, str) or not object_id.strip():
            raise ValueError("Captured Copilot token has an invalid oid claim.")

        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
            raise ValueError("Captured Copilot token has an invalid exp claim.")

        if expires_at - time.time() <= self._expiry_buffer_seconds:
            raise ValueError("Captured Copilot token is within the expiry buffer.")

        self._claims = claims
        self._access_token = captured_token

        return captured_token

    def _extract_access_token_from_websocket_url(self, *, websocket_url: str) -> str | None:
        """
        Extract the access token from a Copilot Chathub websocket URL.

        Args:
            websocket_url: The full websocket URL containing the access token.

        Returns:
            The access token if present, otherwise None.
        """
        parsed_url = urlparse(websocket_url)
        expected_url = urlparse(self._websocket_base_url)

        if (
            parsed_url.scheme != expected_url.scheme
            or parsed_url.hostname != expected_url.hostname
            or not parsed_url.path.startswith(f"{expected_url.path}/")
        ):
            return None

        tokens = parse_qs(parsed_url.query).get("access_token")
        return tokens[0] if tokens else None

    def _handle_websocket_url(
        self,
        *,
        websocket_url: str,
        token_future: asyncio.Future[str],
    ) -> None:
        """Resolve the token future when a valid Copilot url is observed."""
        if token_future.done():
            return

        access_token = self._extract_access_token_from_websocket_url(websocket_url=websocket_url)
        if access_token:
            token_future.set_result(access_token)

    async def _wait_for_token_async(
        self,
        *,
        token_future: asyncio.Future[str],
    ) -> str:
        """
        Wait for the token to be captured or raise a timeout.

        Args:
            token_future: The future that will be resolved with the access token.

        Returns:
            The captured access token.

        Raises:
            TimeoutError: If the token is not captured within the specified timeout.
        """
        try:
            return await asyncio.wait_for(
                token_future,
                timeout=self._token_capture_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                "Timed out waiting for access token capture. Complete sign-in in the opened browser and try again."
            ) from None

    @staticmethod
    def _create_playwright_context_manager() -> Any:
        """
        Create Playwright's asynchronous context manager.

        Returns:
            An instance of Playwright's asynchronous context manager.

        Raises:
            RuntimeError: If Playwright is not installed.
        """
        try:
            from playwright.async_api import async_playwright  # type: ignore[ty:unresolved-import]
        except ImportError:
            raise RuntimeError(
                "Playwright is required for browser-session authentication. "
                "Install the PyRIT playwright extra and run 'playwright install msedge'."
            ) from None

        return async_playwright()

    async def _ensure_browser_session_async(self) -> Any:
        """
        Ensure that the browser session is initialized and return the page instance.

        Returns:
            The initialized page instance.
        """
        if self._page is not None:
            return self._page

        resources = AsyncExitStack()
        try:
            playwright = await resources.enter_async_context(self._create_playwright_context_manager())
            browser_context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._profile_path),
                channel=self._browser_channel,
                headless=self._headless,
            )
            resources.push_async_callback(browser_context.close)

            pages = browser_context.pages
            page = pages[0] if pages else await browser_context.new_page()
        except BaseException:
            await resources.aclose()
            raise

        self._browser_resources = resources
        self._browser_context = browser_context
        self._page = page
        return page

    async def _minimize_browser_window_async(self, *, page: Any) -> None:
        """
        Minimize the browser window using the Chrome DevTools Protocol (CDP).

        Args:
            page: The page instance representing the browser tab.

        Raises:
            RuntimeError: If minimizing the browser window fails.
        """
        cdp_session = await page.context.new_cdp_session(page)

        try:
            window_info = await cdp_session.send("Browser.getWindowForTarget")
            window_id = window_info["windowId"]

            if not isinstance(window_id, int):
                raise RuntimeError("Edge did not return a valid browser window ID.")

            await cdp_session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "minimized"}},
            )
        finally:
            await cdp_session.detach()

    async def _close_browser_resources_async(self) -> None:
        """Close browser resources on their owning event loop."""
        resources = self._browser_resources

        self._browser_resources = None
        self._browser_context = None
        self._page = None

        if resources is not None:
            await resources.aclose()
