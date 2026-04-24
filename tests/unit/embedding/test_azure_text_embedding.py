# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from unittest.mock import MagicMock, patch

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
    """Test that empty API key is accepted by constructor but would fail on use."""
    os.environ[OpenAITextEmbedding.API_KEY_ENVIRONMENT_VARIABLE] = ""
    # Empty string api_key is accepted by OpenAI constructor
    # It will only fail when actually making a request
    embedding = OpenAITextEmbedding(
        api_key="",
        endpoint="https://mock.azure.com/",
        model_name="gpt-4",
    )
    assert embedding is not None


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
