# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import os
from collections.abc import Callable
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.auth import ensure_async_token_provider
from pyrit.prompt_target.openai.openai_target import OpenAITarget


class _ConcreteOpenAITarget(OpenAITarget):
    """Minimal concrete subclass for testing OpenAITarget auth branches."""

    def _set_openai_env_configuration_vars(self) -> None:
        self.model_name_environment_variable = "TEST_MODEL"
        self.endpoint_environment_variable = "TEST_ENDPOINT"
        self.api_key_environment_variable = "TEST_API_KEY"

    def _get_target_api_paths(self) -> list[str]:
        return []

    def _get_provider_examples(self) -> dict[str, str]:
        return {}

    async def _construct_message_from_response(self, response, request):
        raise NotImplementedError

    def _validate_request(self, *, normalized_conversation) -> None:
        pass

    async def _send_prompt_to_target_async(self, *, normalized_conversation):
        raise NotImplementedError


def _build_target(
    *,
    endpoint: str = "https://test.openai.azure.com/openai/v1",
    api_key: Optional[str | Callable] = "test-key",
    env_vars: Optional[dict[str, str]] = None,
) -> _ConcreteOpenAITarget:
    """Helper to build a _ConcreteOpenAITarget with controlled env."""
    env = {"TEST_MODEL": "gpt-4", "TEST_ENDPOINT": endpoint}
    if env_vars:
        env.update(env_vars)
    with patch.dict(os.environ, env, clear=True):
        return _ConcreteOpenAITarget(
            model_name="gpt-4",
            endpoint=endpoint,
            api_key=api_key,
        )


@pytest.mark.usefixtures("patch_central_database")
class TestOpenAITargetAuthResolution:
    """Tests for OpenAITarget.__init__ API key resolution branches."""

    def test_explicit_string_api_key_used_directly(self):
        """When a string api_key is passed, it is used directly."""
        target = _build_target(api_key="my-secret-key")
        assert target._api_key == "my-secret-key"

    def test_env_var_api_key_used_when_no_param(self):
        """When api_key param is None, the env var is read."""
        target = _build_target(api_key=None, env_vars={"TEST_API_KEY": "env-key"})
        assert target._api_key == "env-key"

    def test_non_azure_endpoint_without_key_raises(self):
        """Non-Azure endpoints must have an API key; otherwise ValueError is raised."""
        with pytest.raises(ValueError, match="TEST_API_KEY is required for non-Azure endpoints"):
            _build_target(
                endpoint="https://api.openai.com/v1",
                api_key=None,
            )

    def test_azure_endpoint_falls_back_to_entra(self):
        """Azure endpoints without a key fall back to get_azure_openai_auth."""
        mock_auth = AsyncMock(return_value="entra-token")
        with patch("pyrit.prompt_target.openai.openai_target.get_azure_openai_auth", return_value=mock_auth):
            target = _build_target(
                endpoint="https://myresource.openai.azure.com/openai/v1",
                api_key=None,
            )
        # The api_key should be the async callable returned by get_azure_openai_auth
        assert target._api_key is mock_auth

    def test_callable_token_provider_bypasses_env_lookup(self):
        """A callable api_key is used directly without checking env vars."""
        provider = MagicMock(return_value="token-from-provider")
        target = _build_target(api_key=provider)
        # Should be wrapped in async (sync callable), but the original provider is inside
        assert callable(target._api_key)

    def test_sync_callable_wrapped_in_async(self):
        """A synchronous callable provider is wrapped in an async function."""

        def sync_provider() -> str:
            return "sync-token"

        target = _build_target(api_key=sync_provider)
        assert asyncio.iscoroutinefunction(target._api_key)
        # Verify the wrapper actually calls through
        token = asyncio.run(target._api_key())
        assert token == "sync-token"

    def test_async_callable_passed_through(self):
        """An async callable provider is used as-is without wrapping."""

        async def async_provider() -> str:
            return "async-token"

        target = _build_target(api_key=async_provider)
        assert target._api_key is async_provider

    def test_param_api_key_takes_precedence_over_env_var(self):
        """When both param and env var are set, the param wins."""
        target = _build_target(api_key="param-key", env_vars={"TEST_API_KEY": "env-key"})
        assert target._api_key == "param-key"


class TestEnsureAsyncTokenProvider:
    """Tests for the ensure_async_token_provider helper function."""

    def test_none_returns_none(self):
        assert ensure_async_token_provider(None) is None

    def test_string_returns_string(self):
        assert ensure_async_token_provider("my-key") == "my-key"

    def test_async_callable_returned_as_is(self):
        async def provider() -> str:
            return "token"

        result = ensure_async_token_provider(provider)
        assert result is provider

    def test_sync_callable_wrapped_to_async(self):
        def provider() -> str:
            return "sync-token"

        result = ensure_async_token_provider(provider)
        assert asyncio.iscoroutinefunction(result)
        assert asyncio.run(result()) == "sync-token"

    def test_non_callable_non_string_returned_as_is(self):
        # Edge case: something that's not a string and not callable
        result = ensure_async_token_provider(42)  # type: ignore[arg-type]
        assert result == 42
