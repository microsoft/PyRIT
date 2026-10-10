# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os

import pytest

from pyrit.auth import get_azure_openai_auth
from pyrit.embedding import OpenAITextEmbedding

_AZURE_KEY_AUTH_DISABLED_REASON = "Azure key-based (local) auth is disabled in our tenant."


@pytest.mark.parametrize(
    ("endpoint_env", "api_key_env", "model_env"),
    [
        pytest.param(
            "OPENAI_EMBEDDING_ENDPOINT",
            None,
            "OPENAI_EMBEDDING_MODEL",
            id="entra",
        ),
        pytest.param(
            "OPENAI_EMBEDDING_ENDPOINT",
            "OPENAI_EMBEDDING_KEY",
            "OPENAI_EMBEDDING_MODEL",
            marks=pytest.mark.skip(reason=_AZURE_KEY_AUTH_DISABLED_REASON),
            id="azure-api-key",
        ),
        pytest.param(
            "PLATFORM_OPENAI_EMBEDDING_ENDPOINT",
            "PLATFORM_OPENAI_EMBEDDING_KEY",
            "PLATFORM_OPENAI_EMBEDDING_MODEL",
            marks=pytest.mark.run_only_if_all_tests,
            id="api-key",
        ),
    ],
)
def test_openai_embedding(
    endpoint_env: str,
    api_key_env: str | None,
    model_env: str,
) -> None:
    endpoint = os.environ[endpoint_env]
    model = os.environ[model_env]
    api_key = os.environ[api_key_env] if api_key_env else get_azure_openai_auth(endpoint)

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
