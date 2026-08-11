# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.embedding import OpenAITextEmbedding


def test_valid_init():
    os.environ[OpenAITextEmbedding.API_KEY_ENVIRONMENT_VARIABLE] = ""
    completion = OpenAITextEmbedding(api_key="xxxxx", endpoint="https://mock.azure.com/", model_name="gpt-4")

    assert completion is not None


def test_valid_init_env():
    os.environ[OpenAITextEmbedding.API_KEY_ENVIRONMENT_VARIABLE] = "xxxxx"
    os.environ[OpenAITextEmbedding.ENDPOINT_URI_ENVIRONMENT_VARIABLE] = "https://testcompletionendpoint"
    os.environ[OpenAITextEmbedding.MODEL_ENVIRONMENT_VARIABLE] = "testcompletiondeployment"

    completion = OpenAITextEmbedding()
    assert completion is not None


def test_invalid_key_raises():
    """An empty API key on a non-Azure endpoint raises ValueError (no Entra fallback)."""
    os.environ[OpenAITextEmbedding.API_KEY_ENVIRONMENT_VARIABLE] = ""
    with pytest.raises(ValueError, match="required for non-Azure endpoints"):
        OpenAITextEmbedding(
            api_key="",
            endpoint="https://api.openai.com/v1",
            model_name="gpt-4",
        )


def test_invalid_endpoint_raises():
    os.environ[OpenAITextEmbedding.ENDPOINT_URI_ENVIRONMENT_VARIABLE] = ""
    with pytest.raises(ValueError):
        OpenAITextEmbedding(
            api_key="xxxxxx",
            model_name="gpt-4",
        )


def test_invalid_deployment_raises():
    os.environ[OpenAITextEmbedding.MODEL_ENVIRONMENT_VARIABLE] = ""
    with pytest.raises(ValueError):
        OpenAITextEmbedding(
            api_key="",
            endpoint="https://mock.azure.com/",
        )


@patch("pyrit.embedding.openai_text_embedding.AsyncOpenAI")
def test_default_uses_api_key_from_env(mock_async_openai):
    """Test that default behavior uses API key from environment."""
    mock_async_client = MagicMock()
    mock_async_openai.return_value = mock_async_client

    # Set required environment variables
    os.environ[OpenAITextEmbedding.API_KEY_ENVIRONMENT_VARIABLE] = "env_api_key"
    os.environ[OpenAITextEmbedding.ENDPOINT_URI_ENVIRONMENT_VARIABLE] = "https://mock.azure.com/"
    os.environ[OpenAITextEmbedding.MODEL_ENVIRONMENT_VARIABLE] = "text-embedding"

    # Create instance without specifying api_key
    embedding = OpenAITextEmbedding()

    # Verify async client was created with API key from environment
    mock_async_openai.assert_called_once_with(
        api_key="env_api_key",
        base_url="https://mock.azure.com/",
    )

    assert embedding._async_client == mock_async_client


@patch("pyrit.embedding.openai_text_embedding.AsyncOpenAI")
def test_callable_api_key_is_passed_to_client(mock_async_openai):
    """Test that callable api_key (token provider) is passed through to async client."""
    mock_async_client = MagicMock()
    mock_async_openai.return_value = mock_async_client

    def mock_token_provider():
        return "mock-token"

    # Set required environment variables
    os.environ[OpenAITextEmbedding.ENDPOINT_URI_ENVIRONMENT_VARIABLE] = "https://mock.azure.com/"
    os.environ[OpenAITextEmbedding.MODEL_ENVIRONMENT_VARIABLE] = "text-embedding"

    # Create instance with token provider
    embedding = OpenAITextEmbedding(api_key=mock_token_provider)

    # Verify async client was created with a callable (ensure_async_token_provider wraps sync→async)
    async_call_args = mock_async_openai.call_args
    assert callable(async_call_args.kwargs["api_key"])
    assert async_call_args.kwargs["base_url"] == "https://mock.azure.com/"

    assert embedding._async_client == mock_async_client


_AZURE_ENDPOINT = "https://foo.openai.azure.com/openai/v1"
_NON_AZURE_ENDPOINT = "https://api.openai.com/v1"


def _build_embedding(
    *,
    endpoint: str = _AZURE_ENDPOINT,
    api_key: str | Callable[[], str | Awaitable[str]] | None = "test-key",
    model_name: str = "text-embedding-3-small",
) -> OpenAITextEmbedding:
    """Build an OpenAITextEmbedding with a cleared environment so env vars don't leak in."""
    with patch.dict(os.environ, {}, clear=True):
        return OpenAITextEmbedding(api_key=api_key, endpoint=endpoint, model_name=model_name)


@patch("pyrit.embedding.openai_text_embedding.AsyncOpenAI")
def test_explicit_string_api_key_used_directly(mock_async_openai):
    """An explicit string api_key is passed to the async client as-is."""
    mock_async_openai.return_value = MagicMock()

    _build_embedding(api_key="my-secret-key")

    assert mock_async_openai.call_args.kwargs["api_key"] == "my-secret-key"


@patch("pyrit.embedding.openai_text_embedding.AsyncOpenAI")
def test_callable_token_provider_used_as_is(mock_async_openai):
    """An async token provider is used directly and not overwritten by env resolution."""
    mock_async_openai.return_value = MagicMock()

    async def async_provider() -> str:
        return "async-token"

    _build_embedding(api_key=async_provider)

    assert mock_async_openai.call_args.kwargs["api_key"] is async_provider


@patch("pyrit.embedding.openai_text_embedding.AsyncOpenAI")
def test_no_key_azure_endpoint_falls_back_to_entra(mock_async_openai):
    """A recognized Azure endpoint with no key mints an Entra token provider."""
    mock_async_openai.return_value = MagicMock()
    mock_auth = AsyncMock(return_value="entra-token")

    with patch("pyrit.auth.openai_auth.get_azure_openai_auth", return_value=mock_auth) as mock_get_auth:
        _build_embedding(api_key=None, endpoint=_AZURE_ENDPOINT)

    mock_get_auth.assert_called_once_with(_AZURE_ENDPOINT)
    assert mock_async_openai.call_args.kwargs["api_key"] is mock_auth


def test_no_key_non_azure_endpoint_raises():
    """A non-Azure endpoint with no key raises ValueError (no Entra fallback)."""
    with pytest.raises(ValueError, match="required for non-Azure endpoints"):
        _build_embedding(api_key=None, endpoint=_NON_AZURE_ENDPOINT)
