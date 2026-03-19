# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.auth import ensure_async_token_provider


class TestTokenProviderWrapping:
    """Test suite for synchronous token provider auto-wrapping functionality."""

    def test_string_api_key_unchanged(self):
        """Test that string API keys are returned unchanged."""
        api_key = "sk-test-key-12345"
        result = ensure_async_token_provider(api_key)
        assert result == api_key
        assert isinstance(result, str)

    def test_none_api_key_unchanged(self):
        """Test that None is returned unchanged."""
        result = ensure_async_token_provider(None)
        assert result is None

    def test_async_token_provider_unchanged(self):
        """Test that async token providers are returned unchanged."""

        async def async_token_provider():
            return "async-token"

        result = ensure_async_token_provider(async_token_provider)
        assert result is async_token_provider
        assert asyncio.iscoroutinefunction(result)

    def test_sync_token_provider_wrapped(self):
        """Test that synchronous token providers are automatically wrapped."""

        def sync_token_provider():
            return "sync-token"

        result = ensure_async_token_provider(sync_token_provider)

        # Should return a different callable (the wrapper)
        assert result is not sync_token_provider
        assert callable(result)
        assert asyncio.iscoroutinefunction(result)

    @pytest.mark.asyncio
    async def test_wrapped_sync_provider_returns_correct_token(self):
        """Test that wrapped synchronous token provider returns the correct token."""

        def sync_token_provider():
            return "my-sync-token"

        wrapped = ensure_async_token_provider(sync_token_provider)

        # Call the wrapped provider
        token = await wrapped()
        assert token == "my-sync-token"

    @pytest.mark.asyncio
    async def test_async_provider_returns_correct_token(self):
        """Test that async token providers work correctly."""

        async def async_token_provider():
            return "my-async-token"

        result = ensure_async_token_provider(async_token_provider)

        # Should be the same function
        assert result is async_token_provider

        # Call the provider
        token = await result()
        assert token == "my-async-token"

    @pytest.mark.asyncio
    async def test_wrapped_sync_provider_called_correctly(self):
        """Test that the wrapped sync provider calls the original function."""
        call_count = 0

        def sync_token_provider():
            nonlocal call_count
            call_count += 1
            return f"token-{call_count}"

        wrapped = ensure_async_token_provider(sync_token_provider)

        # Call multiple times
        token1 = await wrapped()
        token2 = await wrapped()

        assert token1 == "token-1"
        assert token2 == "token-2"
        assert call_count == 2

    def test_sync_provider_wrapping_logs_info(self):
        """Test that wrapping a sync provider logs a debug message."""

        def sync_token_provider():
            return "token"

        with patch("pyrit.auth.azure_auth.logger") as mock_logger:
            ensure_async_token_provider(sync_token_provider)
            mock_logger.debug.assert_called_once()
            call_args = mock_logger.debug.call_args[0][0]
            assert "synchronous token provider" in call_args.lower()
            assert "wrapping" in call_args.lower()


@pytest.mark.usefixtures("patch_central_database")
class TestOpenAITargetWithTokenProviders:
    """Test OpenAITarget initialization with different token provider types."""

    @pytest.mark.asyncio
    async def test_openai_target_with_sync_token_provider(self):
        """Test that OpenAIChatTarget works with synchronous token providers."""
        from pyrit.prompt_target import OpenAIChatTarget

        def sync_token_provider():
            return "sync-token-value"

        with (
            patch("pyrit.prompt_target.openai.openai_target.AsyncOpenAI") as mock_openai,
            patch("pyrit.auth.azure_auth.logger") as mock_logger,
        ):
            mock_client = AsyncMock()
            mock_openai.return_value = mock_client

            target = OpenAIChatTarget(
                endpoint="https://api.openai.com/v1",
                model_name="gpt-4o",
                api_key=sync_token_provider,
            )

            # Verify that debug log was called about wrapping
            mock_logger.debug.assert_called()
            info_call_found = False
            for call in mock_logger.debug.call_args_list:
                if "synchronous token provider" in str(call).lower():
                    info_call_found = True
                    break
            assert info_call_found, "Expected debug log about wrapping sync token provider"

            # Verify AsyncOpenAI was initialized
            mock_openai.assert_called_once()
            call_kwargs = mock_openai.call_args[1]

            # The api_key should be a callable
            api_key_arg = call_kwargs["api_key"]
            assert callable(api_key_arg)
            assert asyncio.iscoroutinefunction(api_key_arg)

            # Verify the wrapped token provider returns correct value
            token = await api_key_arg()
            assert token == "sync-token-value"

    @pytest.mark.asyncio
    async def test_openai_target_with_async_token_provider(self):
        """Test that OpenAIChatTarget works with async token providers."""
        from pyrit.prompt_target import OpenAIChatTarget

        async def async_token_provider():
            return "async-token-value"

        with patch("pyrit.prompt_target.openai.openai_target.AsyncOpenAI") as mock_openai:
            mock_client = AsyncMock()
            mock_openai.return_value = mock_client

            target = OpenAIChatTarget(
                endpoint="https://api.openai.com/v1",
                model_name="gpt-4o",
                api_key=async_token_provider,
            )

            # Verify AsyncOpenAI was initialized
            mock_openai.assert_called_once()
            call_kwargs = mock_openai.call_args[1]

            # The api_key should be the same async callable
            api_key_arg = call_kwargs["api_key"]
            assert api_key_arg is async_token_provider

            # Verify the token provider returns correct value
            token = await api_key_arg()
            assert token == "async-token-value"

    @pytest.mark.asyncio
    async def test_openai_target_with_string_api_key(self):
        """Test that OpenAIChatTarget works with string API keys."""
        from pyrit.prompt_target import OpenAIChatTarget

        with patch("pyrit.prompt_target.openai.openai_target.AsyncOpenAI") as mock_openai:
            mock_client = AsyncMock()
            mock_openai.return_value = mock_client

            target = OpenAIChatTarget(
                endpoint="https://api.openai.com/v1",
                model_name="gpt-4o",
                api_key="sk-string-api-key",
            )

            # Verify AsyncOpenAI was initialized
            mock_openai.assert_called_once()
            call_kwargs = mock_openai.call_args[1]

            # The api_key should be a string
            api_key_arg = call_kwargs["api_key"]
            assert api_key_arg == "sk-string-api-key"
            assert isinstance(api_key_arg, str)

    @pytest.mark.asyncio
    async def test_azure_bearer_token_provider_integration(self):
        """Test integration with Azure bearer token provider pattern."""
        from pyrit.prompt_target import OpenAIChatTarget

        # Simulate get_bearer_token_provider from azure.identity (sync version)
        def mock_sync_bearer_token_provider():
            """Mock synchronous bearer token provider."""
            return "Bearer sync-azure-token"

        with (
            patch("pyrit.prompt_target.openai.openai_target.AsyncOpenAI") as mock_openai,
            patch("pyrit.auth.azure_auth.logger") as mock_logger,
        ):
            mock_client = AsyncMock()
            mock_openai.return_value = mock_client

            target = OpenAIChatTarget(
                endpoint="https://myresource.openai.azure.com/openai/v1",
                model_name="gpt-4o",
                api_key=mock_sync_bearer_token_provider,
            )

            # Verify that sync provider was wrapped
            mock_logger.debug.assert_called()

            # Get the wrapped api_key
            call_kwargs = mock_openai.call_args[1]
            wrapped_provider = call_kwargs["api_key"]

            assert asyncio.iscoroutinefunction(wrapped_provider)

            # Verify it returns the correct token
            token = await wrapped_provider()
            assert token == "Bearer sync-azure-token"
