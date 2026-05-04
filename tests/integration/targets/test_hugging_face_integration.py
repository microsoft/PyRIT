# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration tests for HuggingFace via OpenAI-compatible targets.

The HuggingFace Inference Providers API is OpenAI-compatible, so
OpenAIChatTarget and OpenAIResponseTarget work directly with
the HUGGINGFACE_ENDPOINT and HUGGINGFACE_TOKEN env vars.

Requires (loaded from .env by initialize_pyrit_async):
- HUGGINGFACE_TOKEN: HuggingFace API token
- HUGGINGFACE_ENDPOINT: HuggingFace router URL (e.g. https://router.huggingface.co/v1)
"""

import os

import pytest

from pyrit.models import MessagePiece
from pyrit.prompt_target import OpenAIChatTarget, OpenAIResponseTarget

DEFAULT_HF_MODEL = "meta-llama/Llama-3.1-8B-Instruct"


@pytest.fixture()
def hf_token():
    token = os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        pytest.skip("HUGGINGFACE_TOKEN environment variable is not set")
    return token


@pytest.fixture()
def hf_endpoint():
    endpoint = os.environ.get("HUGGINGFACE_ENDPOINT")
    if not endpoint:
        pytest.skip("HUGGINGFACE_ENDPOINT environment variable is not set")
    return endpoint


@pytest.fixture()
def hf_chat_target(hf_token, hf_endpoint, sqlite_instance) -> OpenAIChatTarget:
    return OpenAIChatTarget(
        endpoint=hf_endpoint,
        api_key=hf_token,
        model_name=DEFAULT_HF_MODEL,
        max_tokens=30,
    )


@pytest.fixture()
def hf_response_target(hf_token, hf_endpoint, sqlite_instance) -> OpenAIResponseTarget:
    return OpenAIResponseTarget(
        endpoint=hf_endpoint,
        api_key=hf_token,
        model_name=DEFAULT_HF_MODEL,
        max_output_tokens=30,
    )


# ============================================================================
# Chat Completions API (/v1/chat/completions)
# ============================================================================


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_chat_completion_basic(hf_chat_target):
    """Verify a simple prompt returns a non-empty response via the HF router."""
    msg = MessagePiece(role="user", original_value="What is 2+2? Answer with just the number.").to_message()
    response = await hf_chat_target.send_prompt_async(message=msg)

    assert response is not None
    assert len(response) >= 1
    text = response[0].message_pieces[0].original_value
    assert isinstance(text, str)
    assert len(text) > 0


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_chat_completion_with_temperature(hf_token, hf_endpoint, sqlite_instance):
    """Verify temperature param is accepted by the HF router."""
    target = OpenAIChatTarget(
        endpoint=hf_endpoint,
        api_key=hf_token,
        model_name=DEFAULT_HF_MODEL,
        max_tokens=30,
        temperature=0.7,
    )

    msg = MessagePiece(role="user", original_value="Say hello in one word.").to_message()
    response = await target.send_prompt_async(message=msg)

    assert response is not None
    assert len(response) >= 1
    text = response[0].message_pieces[0].original_value
    assert isinstance(text, str)
    assert len(text) > 0


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_chat_completion_identifier(hf_chat_target):
    """Verify the component identifier reflects the HF endpoint and model."""
    identifier = hf_chat_target.get_identifier()
    assert "router.huggingface.co" in identifier.params["endpoint"]
    assert identifier.params["model_name"] == DEFAULT_HF_MODEL


# ============================================================================
# Responses API (/v1/responses)
# ============================================================================


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_response_api_basic(hf_response_target):
    """Verify a simple prompt returns a non-empty response via the Responses API."""
    msg = MessagePiece(role="user", original_value="What is 2+2? Answer with just the number.").to_message()
    response = await hf_response_target.send_prompt_async(message=msg)

    assert response is not None
    assert len(response) >= 1
    text = response[0].message_pieces[0].original_value
    assert isinstance(text, str)
    assert len(text) > 0


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_response_api_with_temperature(hf_token, hf_endpoint, sqlite_instance):
    """Verify temperature param is accepted by the Responses API on HF."""
    target = OpenAIResponseTarget(
        endpoint=hf_endpoint,
        api_key=hf_token,
        model_name=DEFAULT_HF_MODEL,
        max_output_tokens=30,
        temperature=0.7,
    )

    msg = MessagePiece(role="user", original_value="Say hello in one word.").to_message()
    response = await target.send_prompt_async(message=msg)

    assert response is not None
    assert len(response) >= 1
    text = response[0].message_pieces[0].original_value
    assert isinstance(text, str)
    assert len(text) > 0


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_response_api_identifier(hf_response_target):
    """Verify the component identifier reflects the HF endpoint and model."""
    identifier = hf_response_target.get_identifier()
    assert "router.huggingface.co" in identifier.params["endpoint"]
    assert identifier.params["model_name"] == DEFAULT_HF_MODEL
