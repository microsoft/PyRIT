# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.auth.copilot_authenticator import CopilotAuthenticator


@pytest.fixture
def mock_env_vars():
    """Mock required environment variables."""

    with patch.dict(
        os.environ,
        {
            "COPILOT_USERNAME": "test@example.com",
            "COPILOT_PASSWORD": "test_password_123",
        },
    ):
        yield


@pytest.fixture
def mock_persistent_cache():
    """Mock msal-extensions persistence."""

    mock_cache = MagicMock()
    mock_cache.load.return_value = None
    mock_cache.save.return_value = None
    return mock_cache


@pytest.fixture
def temp_cache_dir():
    """Create a temporary directory for cache files."""

    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


class TestCopilotAuthenticatorConstants:
    """Test class-level constants."""

    def test_class_constants_defined(self):
        assert hasattr(CopilotAuthenticator, "CACHE_FILE_NAME")
        assert hasattr(CopilotAuthenticator, "EXPIRY_BUFFER_SECONDS")
        assert hasattr(CopilotAuthenticator, "DEFAULT_TOKEN_CAPTURE_TIMEOUT")
        assert hasattr(CopilotAuthenticator, "DEFAULT_ELEMENT_TIMEOUT_SECONDS")
        assert hasattr(CopilotAuthenticator, "DEFAULT_NETWORK_RETRIES")

    def test_constant_values(self):
        assert CopilotAuthenticator.CACHE_FILE_NAME == "copilot_token_cache.bin"
        assert CopilotAuthenticator.EXPIRY_BUFFER_SECONDS == 300
        assert CopilotAuthenticator.DEFAULT_TOKEN_CAPTURE_TIMEOUT == 60
        assert CopilotAuthenticator.DEFAULT_ELEMENT_TIMEOUT_SECONDS == 10
        assert CopilotAuthenticator.DEFAULT_NETWORK_RETRIES == 3


class TestCopilotAuthenticatorInitialization:
    """Test CopilotAuthenticator initialization scenarios."""

    def test_init_with_required_env_vars(self, mock_env_vars, mock_persistent_cache):
        """Test successful initialization with required environment variables."""

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()

            assert authenticator._username == "test@example.com"
            assert authenticator._password == "test_password_123"
            assert authenticator._headless is False
            assert authenticator._maximized is True
            assert authenticator._elements_timeout == 10000
            assert authenticator._token_capture_timeout == 60
            assert authenticator._network_retries == 3
            assert authenticator._fallback_to_plaintext is False

            assert isinstance(authenticator._token_fetch_lock, asyncio.Lock)
            assert authenticator._current_claims == {}
            assert authenticator._token_cache is not None

    def test_init_with_custom_parameters(self, mock_env_vars, mock_persistent_cache):
        """Test initialization with custom parameters."""

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator(
                headless=True,
                maximized=False,
                timeout_for_elements_seconds=20,
                token_capture_timeout_seconds=120,
                network_retries=5,
                fallback_to_plaintext=True,
            )

            assert authenticator._headless is True
            assert authenticator._maximized is False
            assert authenticator._elements_timeout == 20000
            assert authenticator._token_capture_timeout == 120
            assert authenticator._network_retries == 5
            assert authenticator._fallback_to_plaintext is True

    def test_init_missing_env_var_raises_error(self):
        """Test that missing a required environment variable raises ValueError."""

        for missing_var in ["COPILOT_USERNAME", "COPILOT_PASSWORD"]:
            env_vars = {
                "COPILOT_USERNAME": "test@example.com",
                "COPILOT_PASSWORD": "test_password_123",
            }
            env_vars.pop(missing_var)
            with (
                patch.dict(os.environ, env_vars, clear=True),
                pytest.raises(
                    ValueError, match="COPILOT_USERNAME and COPILOT_PASSWORD environment variables must be set"
                ),
            ):
                CopilotAuthenticator()

    def test_init_creates_cache_directory(self, mock_env_vars, mock_persistent_cache, temp_cache_dir):
        """Test that initialization creates cache directory if it doesn't exist."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("pyrit.auth.copilot_authenticator.PYRIT_CACHE_PATH", temp_cache_dir / "new_cache"),
        ):
            authenticator = CopilotAuthenticator()

            assert (temp_cache_dir / "new_cache").exists()
            assert authenticator._cache_dir == temp_cache_dir / "new_cache"


class TestCopilotAuthenticatorCacheManagement:
    """Test cache creation, loading, and saving functionality."""

    def test_create_persistent_cache_with_encryption(self):
        """Test cache creation with encryption enabled."""

        mock_encrypted_cache = MagicMock()

        with patch(
            "pyrit.auth.copilot_authenticator.build_encrypted_persistence",
            return_value=mock_encrypted_cache,
        ) as mock_build:
            result = CopilotAuthenticator._create_persistent_cache("/test/cache.bin", fallback_to_plaintext=False)

            mock_build.assert_called_once_with("/test/cache.bin")
            assert result == mock_encrypted_cache

    def test_create_persistent_cache_fallback_to_plaintext(self):
        """Test cache creation falls back to plaintext when encryption fails."""

        mock_plaintext_cache = MagicMock()

        with (
            patch(
                "pyrit.auth.copilot_authenticator.build_encrypted_persistence",
                side_effect=Exception("Encryption not available"),
            ),
            patch(
                "pyrit.auth.copilot_authenticator.FilePersistence",
                return_value=mock_plaintext_cache,
            ) as mock_file_persistence,
        ):
            result = CopilotAuthenticator._create_persistent_cache("/test/cache.bin", fallback_to_plaintext=True)

            mock_file_persistence.assert_called_once_with("/test/cache.bin")
            assert result == mock_plaintext_cache

    def test_create_persistent_cache_raises_on_encryption_failure_without_fallback(self):
        """Test cache creation raises exception when encryption fails and no fallback."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.build_encrypted_persistence",
                side_effect=Exception("Encryption not available"),
            ),
            pytest.raises(Exception, match="Encryption not available"),
        ):
            CopilotAuthenticator._create_persistent_cache("/test/cache.bin", fallback_to_plaintext=False)

    def test_save_token_to_cache_with_expiry(self, mock_env_vars, mock_persistent_cache):
        """Test saving token to cache with expiration time."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("jwt.decode") as mock_jwt_decode,
        ):
            mock_claims = {"upn": "test@example.com", "aud": "sydney"}
            mock_jwt_decode.return_value = mock_claims

            authenticator = CopilotAuthenticator()
            test_token = "test.jwt.token"

            authenticator._save_token_to_cache(token=test_token, expires_in=3600)

            assert mock_persistent_cache.save.called
            saved_data = json.loads(mock_persistent_cache.save.call_args[0][0])

            assert saved_data["access_token"] == test_token
            assert saved_data["token_type"] == "Bearer"
            assert saved_data["claims"] == mock_claims
            assert saved_data["expires_in"] == 3600
            assert "expires_at" in saved_data
            assert "cached_at" in saved_data
            assert authenticator._current_claims == mock_claims

    def test_save_token_to_cache_without_expiry(self, mock_env_vars, mock_persistent_cache):
        """Test saving token to cache without expiration time."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("jwt.decode") as mock_jwt_decode,
        ):
            mock_claims = {"upn": "test@example.com"}
            mock_jwt_decode.return_value = mock_claims

            authenticator = CopilotAuthenticator()
            test_token = "test.jwt.token"

            authenticator._save_token_to_cache(token=test_token, expires_in=None)

            saved_data = json.loads(mock_persistent_cache.save.call_args[0][0])

            assert "expires_in" not in saved_data
            assert "expires_at" not in saved_data
            assert saved_data["access_token"] == test_token

    def test_save_token_handles_jwt_decode_failure(self, mock_env_vars, mock_persistent_cache):
        """Test that save_token handles JWT decode failures gracefully."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("jwt.decode", side_effect=Exception("Invalid JWT")),
            patch("pyrit.auth.copilot_authenticator.logger") as mock_logger,
        ):
            authenticator = CopilotAuthenticator()
            test_token = "invalid.jwt.token"

            authenticator._save_token_to_cache(token=test_token, expires_in=3600)
            mock_logger.error.assert_called_with("Failed to decode token for caching: Invalid JWT")

            saved_data = json.loads(mock_persistent_cache.save.call_args[0][0])
            assert saved_data["access_token"] == test_token
            assert saved_data["claims"] == {}

    def test_clear_token_cache(self, mock_env_vars, mock_persistent_cache):
        """Test clearing the token cache."""

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            authenticator._clear_token_cache()
            mock_persistent_cache.save.assert_called_with(json.dumps({}))

    def test_clear_token_cache_handles_error(self, mock_env_vars, mock_persistent_cache):
        """Test that clear_token_cache handles errors gracefully."""

        mock_persistent_cache.save.side_effect = Exception("Cache error")

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("pyrit.auth.copilot_authenticator.logger") as mock_logger,
        ):
            authenticator = CopilotAuthenticator()
            authenticator._clear_token_cache()
            mock_logger.error.assert_called_with("Failed to clear cache: Cache error")


class TestCopilotAuthenticatorCachedTokenRetrieval:
    """Test cached token validation and retrieval."""

    def test_get_cached_token_valid(self, mock_env_vars, mock_persistent_cache):
        """Test retrieving valid cached token."""

        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        cached_data = {
            "access_token": "cached.token.value",
            "token_type": "Bearer",
            "claims": {"upn": "test@example.com"},
            "expires_at": expires_at,
            "cached_at": datetime.now(timezone.utc).timestamp(),
        }
        mock_persistent_cache.load.return_value = json.dumps(cached_data)

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            result = asyncio.run(authenticator._get_cached_token_if_available_and_valid())
            assert result is not None
            assert result["access_token"] == "cached.token.value"

    def test_get_cached_token_expired(self, mock_env_vars, mock_persistent_cache):
        """Test that expired token is not returned."""

        expires_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()
        cached_data = {
            "access_token": "expired.token.value",
            "claims": {"upn": "test@example.com"},
            "expires_at": expires_at,
        }
        mock_persistent_cache.load.return_value = json.dumps(cached_data)

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            result = asyncio.run(authenticator._get_cached_token_if_available_and_valid())
            assert result is None

    def test_get_cached_token_within_expiry_buffer(self, mock_env_vars, mock_persistent_cache):
        """Test that token within expiry buffer is not returned."""

        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=200)).timestamp()
        cached_data = {
            "access_token": "soon.to.expire",
            "claims": {"upn": "test@example.com"},
            "expires_at": expires_at,
        }
        mock_persistent_cache.load.return_value = json.dumps(cached_data)

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            result = asyncio.run(authenticator._get_cached_token_if_available_and_valid())
            assert result is None  # default buffer is 300 seconds, so should return None

    def test_get_cached_token_no_cache_file(self, mock_env_vars, mock_persistent_cache):
        """Test behavior when cache file doesn't exist."""

        mock_persistent_cache.load.return_value = None
        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            result = asyncio.run(authenticator._get_cached_token_if_available_and_valid())
            assert result is None

    def test_get_cached_token_wrong_user(self, mock_env_vars, mock_persistent_cache):
        """Test that cached token for different user is invalidated."""

        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        cached_data = {
            "access_token": "other.user.token",
            "claims": {"upn": "different@example.com"},
            "expires_at": expires_at,
        }
        mock_persistent_cache.load.return_value = json.dumps(cached_data)

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            result = asyncio.run(authenticator._get_cached_token_if_available_and_valid())
            assert result is None

    def test_get_cached_token_no_upn_in_claims(self, mock_env_vars, mock_persistent_cache):
        """Test that cached token without upn claim is invalidated."""

        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        cached_data = {
            "access_token": "token.without.upn",
            "claims": {"aud": "sydney"},
            "expires_at": expires_at,
        }
        mock_persistent_cache.load.return_value = json.dumps(cached_data)

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            result = asyncio.run(authenticator._get_cached_token_if_available_and_valid())
            assert result is None

    def test_get_cached_token_missing_access_token(self, mock_env_vars, mock_persistent_cache):
        """Test that cache data without access_token is invalid."""

        cached_data = {
            "token_type": "Bearer",
            "claims": {"upn": "test@example.com"},
        }
        mock_persistent_cache.load.return_value = json.dumps(cached_data)

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            result = asyncio.run(authenticator._get_cached_token_if_available_and_valid())
            assert result is None

    def test_get_cached_token_invalid_json(self, mock_env_vars, mock_persistent_cache):
        """Test handling of corrupted cache data."""

        mock_persistent_cache.load.return_value = "invalid json {{"

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            result = asyncio.run(authenticator._get_cached_token_if_available_and_valid())
            assert result is None


class TestCopilotAuthenticatorTokenRetrieval:
    """Test token retrieval via get_token method."""

    @pytest.mark.asyncio
    async def test_get_token_uses_cached_token(self, mock_env_vars, mock_persistent_cache):
        """Test that get_token uses cached token when available."""

        expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        cached_data = {
            "access_token": "cached.valid.token",
            "claims": {"upn": "test@example.com"},
            "expires_at": expires_at,
        }
        mock_persistent_cache.load.return_value = json.dumps(cached_data)

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            token = await authenticator.get_token_async()
            assert token == "cached.valid.token"

    @pytest.mark.asyncio
    async def test_get_token_fetches_new_when_no_cache(self, mock_env_vars, mock_persistent_cache):
        """Test that get_token fetches new token when cache is empty."""

        mock_persistent_cache.load.return_value = None
        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._fetch_access_token_with_playwright",
                new_callable=AsyncMock,
                return_value="new.fetched.token",
            ) as mock_fetch,
        ):
            authenticator = CopilotAuthenticator()
            token = await authenticator.get_token_async()
            mock_fetch.assert_called_once()
            assert token == "new.fetched.token"

    @pytest.mark.asyncio
    async def test_get_token_serializes_concurrent_requests(self, mock_env_vars, mock_persistent_cache):
        """Test that concurrent get_token calls are serialized via lock."""

        fetch_call_count = 0
        mock_persistent_cache.load.return_value = None  # start with no cache

        async def mock_fetch():
            nonlocal fetch_call_count
            fetch_call_count += 1
            await asyncio.sleep(0.01)  # minimal delay to test concurrency
            return f"token.{fetch_call_count}"

        def mock_load_side_effect():
            # After first fetch, return cached token for subsequent calls
            if fetch_call_count > 0:
                expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
                return json.dumps(
                    {
                        "access_token": "token.1",
                        "claims": {"upn": "test@example.com"},
                        "expires_at": expires_at,
                    }
                )
            return None

        mock_persistent_cache.load.side_effect = mock_load_side_effect
        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._fetch_access_token_with_playwright",
                new_callable=AsyncMock,
                side_effect=mock_fetch,
            ),
            patch("jwt.decode", return_value={"upn": "test@example.com"}),
        ):
            authenticator = CopilotAuthenticator()

            results = await asyncio.gather(
                authenticator.get_token_async(),
                authenticator.get_token_async(),
                authenticator.get_token_async(),
            )

            # Only one fetch should have occurred due to lock + caching
            assert fetch_call_count == 1
            assert results[0] == results[1] == results[2] == "token.1"


class TestCopilotAuthenticatorTokenRefresh:
    """Test token refresh functionality."""

    @pytest.mark.asyncio
    async def test_refresh_token_clears_cache(self, mock_env_vars, mock_persistent_cache):
        """Test that refresh_token clears existing cache."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._fetch_access_token_with_playwright",
                new_callable=AsyncMock,
                return_value="refreshed.token",
            ),
        ):
            authenticator = CopilotAuthenticator()
            await authenticator.refresh_token_async()
            assert any(json.dumps({}) in str(call) for call in mock_persistent_cache.save.call_args_list)

    @pytest.mark.asyncio
    async def test_refresh_token_fetches_new_token(self, mock_env_vars, mock_persistent_cache):
        """Test that refresh_token fetches new token."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._fetch_access_token_with_playwright",
                new_callable=AsyncMock,
                return_value="refreshed.token",
            ) as mock_fetch,
        ):
            authenticator = CopilotAuthenticator()
            token = await authenticator.refresh_token_async()
            mock_fetch.assert_called_once()
            assert token == "refreshed.token"

    @pytest.mark.asyncio
    async def test_refresh_token_raises_on_failure(self, mock_env_vars, mock_persistent_cache):
        """Test that refresh_token raises exception when fetch fails."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._fetch_access_token_with_playwright",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            authenticator = CopilotAuthenticator()
            with pytest.raises(RuntimeError, match="Failed to refresh access token"):
                await authenticator.refresh_token_async()

    @pytest.mark.asyncio
    async def test_refresh_token_clears_current_claims(self, mock_env_vars, mock_persistent_cache):
        """Test that refresh_token clears current claims."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._fetch_access_token_with_playwright",
                new_callable=AsyncMock,
                return_value="refreshed.token",
            ),
        ):
            authenticator = CopilotAuthenticator()
            authenticator._current_claims = {"old": "claims"}
            await authenticator.refresh_token_async()
            assert authenticator._current_claims == {}


class TestCopilotAuthenticatorGetClaims:
    """Test JWT claims retrieval."""

    @pytest.mark.asyncio
    async def test_get_claims_returns_current_claims(self, mock_env_vars, mock_persistent_cache):
        """Test that get_claims returns current claims."""

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            test_claims = {"upn": "test@example.com", "aud": "sydney"}
            authenticator._current_claims = test_claims
            claims = await authenticator.get_claims()
            assert claims == test_claims

    @pytest.mark.asyncio
    async def test_get_claims_returns_empty_dict_when_no_claims(self, mock_env_vars, mock_persistent_cache):
        """Test that get_claims returns empty dict when no claims set."""

        with patch(
            "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
            return_value=mock_persistent_cache,
        ):
            authenticator = CopilotAuthenticator()
            claims = await authenticator.get_claims()
            assert claims == {}


class TestCopilotAuthenticatorPlaywrightIntegration:
    """Test Playwright browser automation (mocked)."""

    @pytest.fixture(autouse=True)
    def mock_playwright_module(self):
        """
        Inject a mock playwright module into sys.modules.

        This allows tests to run without playwright installed by providing
        a mock module that can be patched.
        """
        import sys

        mock_async_api = MagicMock()
        mock_playwright_module = MagicMock()
        mock_playwright_module.async_api = mock_async_api

        # Store original modules if they exist
        orig_playwright = sys.modules.get("playwright")
        orig_async_api = sys.modules.get("playwright.async_api")

        # Inject mock modules
        sys.modules["playwright"] = mock_playwright_module
        sys.modules["playwright.async_api"] = mock_async_api

        yield mock_async_api

        # Restore original state
        if orig_playwright is not None:
            sys.modules["playwright"] = orig_playwright
        else:
            sys.modules.pop("playwright", None)

        if orig_async_api is not None:
            sys.modules["playwright.async_api"] = orig_async_api
        else:
            sys.modules.pop("playwright.async_api", None)

    @pytest.mark.asyncio
    async def test_fetch_token_playwright_not_installed(self, mock_env_vars, mock_persistent_cache):
        """Test that RuntimeError is raised when Playwright is not installed."""

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch.dict("sys.modules", {"playwright.async_api": None}),
        ):
            authenticator = CopilotAuthenticator()
            with pytest.raises(RuntimeError, match="Playwright is not installed"):
                await authenticator._fetch_access_token_with_playwright()

    @pytest.mark.asyncio
    async def test_fetch_token_with_playwright_success(self, mock_env_vars, mock_persistent_cache):
        """Test successful token fetch with Playwright."""

        # Setup mock Playwright objects
        mock_page = AsyncMock()
        mock_context = AsyncMock()
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()

        # Setup mock browser hierarchy
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.close = AsyncMock()
        mock_context.close = AsyncMock()

        # Mock page methods
        mock_page.goto = AsyncMock()
        mock_page.wait_for_selector = AsyncMock()
        mock_page.fill = AsyncMock()

        # Create a response that will trigger the token capture
        mock_response = AsyncMock()
        mock_response.url = "https://login.microsoftonline.com/oauth2/v2.0/token"
        mock_response.text = AsyncMock(
            return_value=(
                '{"access_token":"captured.bearer.token","token_type":"Bearer","expires_in":3600,"resource":"sydney"}'
            )
        )

        response_handler = None

        def capture_handler(event, handler):
            """Capture the response handler when page.on() is called."""
            nonlocal response_handler
            if event == "response":
                response_handler = handler

        mock_page.on = MagicMock(side_effect=capture_handler)

        async def trigger_response_on_click(*args, **kwargs):
            """Trigger the response handler when click is called."""
            if response_handler:
                await response_handler(mock_response)

        mock_page.click = AsyncMock(side_effect=trigger_response_on_click)

        mock_async_playwright = AsyncMock()
        mock_async_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_async_playwright.__aexit__ = AsyncMock()

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("playwright.async_api.async_playwright", return_value=mock_async_playwright),
            patch("jwt.decode", return_value={"upn": "test@example.com"}),
        ):
            authenticator = CopilotAuthenticator()
            token = await authenticator._fetch_access_token_with_playwright()

            assert token == "captured.bearer.token"
            mock_browser.close.assert_called_once()
            mock_context.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_token_handles_browser_launch_failure(self, mock_env_vars, mock_persistent_cache):
        """Test handling of browser launch failure."""

        mock_playwright = AsyncMock()
        mock_playwright.chromium.launch = AsyncMock(side_effect=Exception("BrowserType.launch failed"))

        mock_async_playwright = AsyncMock()
        mock_async_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_async_playwright.__aexit__ = AsyncMock()

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("playwright.async_api.async_playwright", return_value=mock_async_playwright),
        ):
            authenticator = CopilotAuthenticator()
            token = await authenticator._fetch_access_token_with_playwright()
            assert token is None

    @pytest.mark.asyncio
    async def test_fetch_token_sanitizes_password_in_errors(self, mock_env_vars, mock_persistent_cache):
        """Test that password is sanitized in error messages."""

        mock_playwright = AsyncMock()
        error_with_password = Exception("Login failed with password: test_password_123")
        mock_playwright.chromium.launch = AsyncMock(side_effect=error_with_password)

        mock_async_playwright = AsyncMock()
        mock_async_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_async_playwright.__aexit__ = AsyncMock()

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("playwright.async_api.async_playwright", return_value=mock_async_playwright),
            patch("pyrit.auth.copilot_authenticator.logger") as mock_logger,
        ):
            authenticator = CopilotAuthenticator()
            await authenticator._fetch_access_token_with_playwright()

            # Verify password was sanitized in error log
            logged_messages = [str(call) for call in mock_logger.error.call_args_list]
            assert any("******" in msg for msg in logged_messages)
            assert not any("test_password_123" in msg for msg in logged_messages)

    @pytest.mark.asyncio
    async def test_fetch_token_timeout_waiting_for_token(self, mock_env_vars, mock_persistent_cache):
        """Test timeout when waiting for token capture."""

        mock_page = AsyncMock()
        mock_context = AsyncMock()
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()

        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.close = AsyncMock()
        mock_context.close = AsyncMock()

        mock_page.goto = AsyncMock()
        mock_page.wait_for_selector = AsyncMock()
        mock_page.click = AsyncMock()
        mock_page.fill = AsyncMock()
        mock_page.on = MagicMock()

        mock_async_playwright = AsyncMock()
        mock_async_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_async_playwright.__aexit__ = AsyncMock()

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("playwright.async_api.async_playwright", return_value=mock_async_playwright),
            patch("asyncio.sleep", new_callable=AsyncMock),  # mock sleep to speed up test
        ):
            authenticator = CopilotAuthenticator(token_capture_timeout_seconds=1)
            token = await authenticator._fetch_access_token_with_playwright()
            assert token is None
            mock_browser.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_token_closes_browser_on_exception(self, mock_env_vars, mock_persistent_cache):
        """Test that browser is closed even when exception occurs."""

        mock_page = AsyncMock()
        mock_context = AsyncMock()
        mock_browser = AsyncMock()
        mock_playwright = AsyncMock()

        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.close = AsyncMock()
        mock_context.close = AsyncMock()

        mock_page.goto = AsyncMock(side_effect=Exception("Navigation failed"))
        mock_page.on = MagicMock()  # page.on is synchronous in Playwright

        mock_async_playwright = AsyncMock()
        mock_async_playwright.__aenter__ = AsyncMock(return_value=mock_playwright)
        mock_async_playwright.__aexit__ = AsyncMock()

        with (
            patch(
                "pyrit.auth.copilot_authenticator.CopilotAuthenticator._create_persistent_cache",
                return_value=mock_persistent_cache,
            ),
            patch("playwright.async_api.async_playwright", return_value=mock_async_playwright),
        ):
            authenticator = CopilotAuthenticator()
            token = await authenticator._fetch_access_token_with_playwright()
            assert token is None
            mock_context.close.assert_called_once()
            mock_browser.close.assert_called_once()
