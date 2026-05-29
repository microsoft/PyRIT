# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from msal_extensions import FilePersistence, build_encrypted_persistence

from pyrit.auth.authenticator import Authenticator
from pyrit.common.path import PYRIT_CACHE_PATH

logger = logging.getLogger(__name__)


class CopilotAuthenticator(Authenticator):
    """
    Playwright-based authenticator for Microsoft Copilot. Used by WebSocketCopilotTarget.

    This authenticator automates browser login to obtain and refresh access tokens that are necessary
    for accessing Microsoft Copilot via WebSocket connections. It uses Playwright to simulate user
    interactions for authentication, and msal-extensions for encrypted token persistence.

    An access token acquired by this authenticator is usually valid for about 60 minutes.

    Note:
        To be able to use this authenticator, you must set the following environment variables:
            - COPILOT_USERNAME: your Microsoft account username (email)
            - COPILOT_PASSWORD: your Microsoft account password

        Additionally, you need to have playwright installed and set up:
        ``pip install playwright && playwright install chromium``.
    """

    #: Name of the cache file to store tokens
    CACHE_FILE_NAME: str = "copilot_token_cache.bin"
    #: Buffer before token expiry to avoid using tokens about to expire (in seconds)
    EXPIRY_BUFFER_SECONDS: int = 300
    #: Default timeout for capturing token via network monitoring (in seconds)
    DEFAULT_TOKEN_CAPTURE_TIMEOUT: int = 60
    #: Default timeout for waiting on page elements (in seconds)
    DEFAULT_ELEMENT_TIMEOUT_SECONDS: int = 10
    #: Number of retries for network operations
    DEFAULT_NETWORK_RETRIES: int = 3

    def __init__(
        self,
        *,
        headless: bool = False,
        maximized: bool = True,
        timeout_for_elements_seconds: int = DEFAULT_ELEMENT_TIMEOUT_SECONDS,
        token_capture_timeout_seconds: int = DEFAULT_TOKEN_CAPTURE_TIMEOUT,
        network_retries: int = DEFAULT_NETWORK_RETRIES,
        fallback_to_plaintext: bool = False,
    ) -> None:
        """
        Initialize the CopilotAuthenticator.

        Args:
            headless (bool): Whether to run the browser in headless mode. Default is False.
            maximized (bool): Whether to start the browser maximized. Default is True.
            timeout_for_elements_seconds (int): Timeout used when waiting for page elements, in seconds.
            token_capture_timeout_seconds (int): Maximum time to wait for token capture via network monitoring.
            network_retries (int): Number of retry attempts for network operations. Default is 3.
            fallback_to_plaintext (bool): Whether to fallback to plaintext storage if encryption is unavailable.
                If set to False (default), an exception will be raised if encryption cannot be used.
                WARNING: Setting to True stores tokens in plaintext.

        Raises:
            ValueError: If the required environment variables are not set.
        """
        super().__init__()

        self._username = os.getenv("COPILOT_USERNAME")
        self._password = os.getenv("COPILOT_PASSWORD")

        self._headless = headless
        self._maximized = maximized
        self._elements_timeout = timeout_for_elements_seconds * 1000
        self._token_capture_timeout = token_capture_timeout_seconds
        self._network_retries = network_retries
        self._fallback_to_plaintext = fallback_to_plaintext

        self._cache_dir = PYRIT_CACHE_PATH
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = str(self._cache_dir / self.CACHE_FILE_NAME)

        if not self._username or not self._password:
            raise ValueError("COPILOT_USERNAME and COPILOT_PASSWORD environment variables must be set.")

        self._token_cache = self._create_persistent_cache(self._cache_file, self._fallback_to_plaintext)
        self._current_claims: dict[str, Any] = {}  # for easy access to claims without re-decoding token

        # Lock to prevent concurrent token fetches from launching multiple browsers
        self._token_fetch_lock = asyncio.Lock()

    async def refresh_token_async(self) -> str:
        """
        Refresh the authentication token asynchronously.

        This will clear the existing token cache and fetch a new token with automated browser login.

        Returns:
            str: The refreshed authentication token.

        Raises:
            RuntimeError: If token refresh fails.
        """
        logger.info("Refreshing access token...")
        self._clear_token_cache()
        self._current_claims = {}
        token = await self._fetch_access_token_with_playwright()

        if not token:
            raise RuntimeError("Failed to refresh access token.")

        return token

    async def get_token_async(self) -> str:
        """
        Get the current authentication token.

        This checks the cache first and only launches the browser if no valid token is found.
        If multiple calls are made concurrently, they will be serialized via an asyncio lock
        to prevent launching multiple browser instances.

        Returns:
            str: A valid Bearer token for Microsoft Copilot.
        """
        async with self._token_fetch_lock:
            cached_token = await self._get_cached_token_if_available_and_valid()
            if cached_token and "access_token" in cached_token:
                logger.info("Using cached access token.")
                if "claims" in cached_token:
                    self._current_claims = cached_token["claims"]
                return str(cached_token["access_token"])

            logger.info("No valid cached token found. Initiating browser authentication.")
            return await self.refresh_token_async()

    async def get_claims(self) -> dict[str, Any]:
        """
        Get the JWT claims from the current authentication token.

        Returns:
            dict[str, Any]: The JWT claims decoded from the access token.
        """
        return self._current_claims or {}

    @staticmethod
    def _create_persistent_cache(cache_file: str, fallback_to_plaintext: bool = False) -> Any:
        """
        Create a persistent cache for token storage with encryption.

        Uses msal-extensions to provide encrypted storage. Falls back to plaintext
        only if explicitly allowed and encryption is unavailable.

        Args:
            cache_file: Path to the cache file.
            fallback_to_plaintext: Whether to allow plaintext fallback.

        Returns:
            A persistence object (encrypted or plaintext).

        Raises:
            Exception: If encryption fails and fallback is not allowed.
        """
        # https://github.com/AzureAD/microsoft-authentication-extensions-for-python

        try:
            logger.info(f"Using encrypted persistent token cache: {cache_file}")
            return build_encrypted_persistence(cache_file)
        except Exception as e:
            if fallback_to_plaintext:
                logger.warning(f"Encryption unavailable ({e}). Falling back to PLAINTEXT storage.")
                return FilePersistence(cache_file)
            logger.error(f"Encryption unavailable ({e}) and fallback_to_plaintext is False. Cannot proceed.")
            raise

    async def _get_cached_token_if_available_and_valid(self) -> Optional[dict[str, Any]]:
        """
        Retrieve and validate cached token.

        Validates that:
        - Token exists and is properly formatted.
        - Token belongs to the current user (username match).
        - Token has not expired (with safety buffer).

        Returns:
            Token data dictionary if valid, None otherwise.
        """
        try:
            cache_data = self._token_cache.load()
            if not cache_data:
                logger.info("No cached token data found.")
                return None

            token_data = json.loads(cache_data)
            if "access_token" not in token_data:
                logger.info("No access token in cache.")
                return None

            cached_user = token_data.get("claims").get("upn")
            if not cached_user:
                logger.info("No user associated with cached token. Token invalidated.")
                return None
            if cached_user != self._username:
                logger.info(
                    f"Cached token is for different user (cached: {cached_user}, current: {self._username}). "
                    "Token invalidated."
                )
                return None

            expires_at = token_data.get("expires_at")
            if expires_at:
                expiry_time = datetime.fromtimestamp(expires_at, tz=timezone.utc)
                current_time = datetime.now(timezone.utc)

                # This should prevent most mid-request failures due to token expiration
                expiry_with_buffer = expiry_time - timedelta(seconds=self.EXPIRY_BUFFER_SECONDS)
                if current_time >= expiry_with_buffer:
                    minutes_until_expiry = (expiry_time - current_time).total_seconds() / 60
                    logger.info(
                        f"Cached token expires in {minutes_until_expiry:.2f} minutes, "
                        f"within {self.EXPIRY_BUFFER_SECONDS}s safety buffer. Token invalidated."
                    )
                    return None

                minutes_left = (expiry_time - current_time).total_seconds() / 60
                logger.info(f"Cached token is valid for another {minutes_left:.2f} minutes")

            return token_data

        except Exception as e:
            error_name = type(e).__name__
            if "PersistenceNotFound" in error_name or "FileNotFoundError" in error_name:
                logger.info("Cache file does not exist yet. Will be created on first token save.")
            else:
                logger.error(f"Failed to load cached token ({error_name}): {e}")
            return None

    def _save_token_to_cache(self, *, token: str, expires_in: Optional[int] = None) -> None:
        """
        Save token to persistent cache with metadata.

        Args:
            token: The access token to cache.
            expires_in: Token lifetime in seconds (optional).
        """
        self._current_claims = {}

        try:
            import jwt

            self._current_claims = jwt.decode(token, algorithms=["RS256"], options={"verify_signature": False})

        except Exception as e:
            logger.error(f"Failed to decode token for caching: {e}")

        token_data = {
            "access_token": token,
            "token_type": "Bearer",
            "claims": self._current_claims,
            "cached_at": datetime.now(timezone.utc).timestamp(),
        }

        if expires_in:
            expires_at = datetime.now(timezone.utc).timestamp() + expires_in
            token_data["expires_at"] = expires_at
            token_data["expires_in"] = expires_in

        try:
            self._token_cache.save(json.dumps(token_data))
            logger.info("Token successfully cached.")
        except Exception as e:
            logger.error(f"Failed to cache token: {e}")

    def _clear_token_cache(self) -> None:
        try:
            self._token_cache.save(json.dumps({}))
            logger.info("Token cache cleared.")
        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")

    async def _fetch_access_token_with_playwright(self) -> Optional[str]:
        """
        Fetch access token using Playwright browser automation.

        Returns:
            Optional[str]: The bearer token if successfully retrieved, None otherwise.

        Raises:
            RuntimeError: If Playwright is not installed or browser launch fails.
        """
        import sys

        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Please install it with: "
                "'pip install playwright && playwright install chromium'"
            ) from None

        # On Windows, when using SelectorEventLoop (common in Jupyter), we need to run
        # Playwright in a separate thread with ProactorEventLoop to support subprocesses
        # https://playwright.dev/python/docs/library#incompatible-with-selectoreventloop-of-asyncio-on-windows
        if sys.platform == "win32":
            try:
                loop = asyncio.get_running_loop()
                if isinstance(loop, asyncio.SelectorEventLoop):
                    logger.info(
                        "Running on Windows with SelectorEventLoop. "
                        "Will run Playwright in a separate thread with ProactorEventLoop."
                    )
                    return await self._run_playwright_in_thread()
            except RuntimeError:
                pass

        # If not on Windows or using the right loop already, proceed normally
        return await self._run_playwright_browser_automation()

    async def _run_playwright_in_thread(self) -> Optional[str]:
        """
        Run Playwright browser automation in a separate thread with ProactorEventLoop.
        This is needed on Windows when the main loop is SelectorEventLoop (e.g., in Jupyter).

        Returns:
            Optional[str]: The bearer token if successfully retrieved, None otherwise.
        """

        def run_in_new_loop() -> Optional[str]:
            if sys.platform == "win32":
                new_loop = asyncio.ProactorEventLoop()
            else:
                new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                result: Optional[str] = new_loop.run_until_complete(self._run_playwright_browser_automation())
                return result
            finally:
                new_loop.close()

        return await asyncio.get_running_loop().run_in_executor(None, run_in_new_loop)

    async def _run_playwright_browser_automation(self) -> Optional[str]:
        """
        Execute the actual Playwright browser automation to fetch the access token.

        Returns:
            Optional[str]: The bearer token if successfully retrieved, None otherwise.

        Raises:
            ValueError: If the username is not set.
        """
        from playwright.async_api import async_playwright

        bearer_token = None
        token_expires_in = None

        async with async_playwright() as playwright:
            browser = None
            context = None

            try:
                logger.info(f"Launching browser for authentication (headless={self._headless})...")
                browser = await playwright.chromium.launch(
                    headless=self._headless, args=["--start-maximized"] if self._maximized else []
                )

                context = await browser.new_context(no_viewport=True)
                page = await context.new_page()

                # response_handler >>>
                async def response_handler(response: Any) -> None:
                    nonlocal bearer_token, token_expires_in

                    try:
                        url = response.url

                        if "/oauth2/v2.0/token" in url:
                            try:
                                text = await response.text()

                                if (
                                    '"token_type":"Bearer"' in text or '"tokenType":"Bearer"' in text
                                ) and "sydney" in text:
                                    try:
                                        data = json.loads(text)
                                        if "access_token" in data:
                                            bearer_token = data["access_token"]
                                            token_expires_in = data.get("expires_in")
                                            logger.info("Captured bearer token from JSON response.")

                                    except Exception as e:
                                        logger.error(f"Error parsing JSON token response: {e}")

                            except Exception as e:
                                logger.error(f"Error reading response: {e}")

                    except Exception as e:
                        logger.error(f"Error handling response: {e}")

                # ^^^ response_handler

                page.on("response", response_handler)

                logger.info("Navigating to Office.com for authentication...")
                await page.goto("https://www.office.com/")

                logger.info("Waiting for profile icon...")
                await page.wait_for_selector("#mectrl_headerPicture", timeout=self._elements_timeout)
                await page.click("#mectrl_headerPicture")

                logger.info("Waiting for email input...")
                await page.wait_for_selector("#i0116", timeout=self._elements_timeout)
                if self._username is None:
                    raise ValueError("Username is not set")
                await page.fill("#i0116", self._username)
                await page.click("#idSIButton9")

                logger.info("Waiting for password input...")
                await page.wait_for_selector("#i0118", timeout=self._elements_timeout)
                if self._password is None:
                    raise ValueError("Password is not set")
                await page.fill("#i0118", self._password)
                await page.click("#idSIButton9")

                logger.info("Waiting for 'Stay signed in?' prompt...")
                await page.wait_for_selector("#idSIButton9", timeout=self._elements_timeout)
                logger.info("Clicking 'Yes' to stay signed in...")
                await page.click("#idSIButton9")

                logger.info("Successfully logged in.")
                logger.info("Navigating to Copilot...")

                logger.info("Waiting for Copilot button and clicking it...")
                await page.wait_for_selector('div[aria-label="M365 Copilot"]', timeout=self._elements_timeout)
                await page.click('div[aria-label="M365 Copilot"]', timeout=self._elements_timeout)

                logger.info(f"Waiting up to {self._token_capture_timeout}s for bearer token to be captured...")
                for elapsed in range(self._token_capture_timeout):
                    if bearer_token:
                        logger.info(f"Token captured after {elapsed}s")
                        break
                    await asyncio.sleep(1)

                if bearer_token:
                    logger.info(
                        f"Bearer token successfully retrieved. Preview: {bearer_token[:16]}...{bearer_token[-16:]}"
                    )
                    self._save_token_to_cache(token=bearer_token, expires_in=token_expires_in)
                else:
                    logger.error(f"Failed to retrieve bearer token within {self._token_capture_timeout} seconds.")

                return bearer_token
            except Exception as e:
                logger.error("Failed to retrieve access token using Playwright.")

                if "BrowserType.launch" in str(e):
                    logger.error("Playwright browser launch failed. Did you run 'playwright install chromium'?")
                else:
                    # Sanitize error message to avoid leaking sensitive info
                    error_msg = str(e)
                    if self._password and self._password in error_msg:
                        error_msg = error_msg.replace(self._password, "******")
                    logger.error(f"Error details: {error_msg}")

                return None
            finally:
                logger.info("Gracefully closing Playwright browser instance...")

                if page:
                    await page.close()
                if context:
                    await context.close()
                if browser:
                    await browser.close()
