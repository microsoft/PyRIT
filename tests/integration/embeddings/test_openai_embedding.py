# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os

import pytest

from pyrit.auth import get_azure_openai_auth
from pyrit.embedding import OpenAITextEmbedding


@pytest.mark.run_only_if_all_tests
@pytest.mark.parametrize(
    "endpoint_env,key_env,model_env",
    [
        ("OPENAI_EMBEDDING_ENDPOINT", "OPENAI_EMBEDDING_KEY", "OPENAI_EMBEDDING_MODEL"),
        ("PLATFORM_OPENAI_EMBEDDING_ENDPOINT", "PLATFORM_OPENAI_EMBEDDING_KEY", "PLATFORM_OPENAI_EMBEDDING_MODEL"),
    ],
)
def test_openai_embedding_with_api_key(endpoint_env: str, key_env: str, model_env: str):
    """Test OpenAI embedding with API key authentication."""
    api_key = os.environ[key_env]
    endpoint = os.environ[endpoint_env]
    model = os.environ[model_env]

    embedding = OpenAITextEmbedding(
        api_key=api_key,
        endpoint=endpoint,
        model_name=model,
    )

    test_text = "Hello, this is a test for embedding generation."
    response = embedding.generate_text_embedding(text=test_text)

    assert response is not None
    assert len(response.data) == 1
    assert len(response.data[0].embedding) > 0
    assert response.usage.total_tokens > 0


def test_azure_openai_embedding_with_entra_auth():
    """Test Azure OpenAI embedding with Entra (token provider) authentication."""
    endpoint = os.environ["OPENAI_EMBEDDING_ENDPOINT"]
    model = os.environ["OPENAI_EMBEDDING_MODEL"]

    # Get token provider for Entra auth
    token_provider = get_azure_openai_auth(endpoint)

    embedding = OpenAITextEmbedding(
        api_key=token_provider,
        endpoint=endpoint,
        model_name=model,
    )

    test_text = "Testing embedding with Entra authentication."
    response = embedding.generate_text_embedding(text=test_text)

    assert response is not None
    assert len(response.data) == 1
    assert len(response.data[0].embedding) > 0
    assert response.usage.total_tokens > 0
