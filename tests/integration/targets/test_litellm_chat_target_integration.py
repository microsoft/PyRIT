# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration tests for LiteLLMChatTarget.

These reuse the existing ``PLATFORM_OPENAI_CHAT_*`` environment configuration to exercise
LiteLLM against a real OpenAI-compatible endpoint (LiteLLM's ``openai/`` provider prefix).

They verify:
- Basic text completion
- Tool calling via the ``extra_body_parameters`` passthrough
- Multimodal image input (vision)
- Multimodal audio input/output
- Token-usage metadata capture (parsed back through ``TokenUsage``)

The chat/vision model defaults to ``gpt-5.4`` and the audio model to ``gpt-audio``; both can be
overridden with the ``PLATFORM_OPENAI_CHAT_MODEL`` / ``PLATFORM_OPENAI_AUDIO_MODEL`` env vars to
match whatever deployment names the target endpoint exposes.
"""

import json
import os
import uuid

import pytest

from pyrit.common.path import HOME_PATH
from pyrit.models import Message, MessagePiece, TokenUsage
from pyrit.prompt_target import (
    LiteLLMChatTarget,
    OpenAIChatAudioConfig,
    TargetCapabilities,
    TargetConfiguration,
)

# Assets reused for multimodal parity checks.
SAMPLE_IMAGE_FILE = HOME_PATH / "assets" / "pyrit_architecture.png"
SAMPLE_AUDIO_FILE = HOME_PATH / "assets" / "converted_audio.wav"


@pytest.fixture()
def platform_litellm_args():
    """
    Requires:
        - PLATFORM_OPENAI_CHAT_ENDPOINT: An OpenAI-compatible endpoint (e.g. https://api.openai.com/v1)
        - PLATFORM_OPENAI_CHAT_KEY: The API key
    """
    endpoint = os.environ.get("PLATFORM_OPENAI_CHAT_ENDPOINT")
    api_key = os.environ.get("PLATFORM_OPENAI_CHAT_KEY")

    if not endpoint or not api_key:
        pytest.skip("PLATFORM_OPENAI_CHAT_ENDPOINT and PLATFORM_OPENAI_CHAT_KEY must be set")

    model = os.environ.get("PLATFORM_OPENAI_CHAT_MODEL", "gpt-5.4")
    return {
        "model_name": f"openai/{model}",
        "endpoint": endpoint,
        "api_key": api_key,
    }


@pytest.fixture()
def platform_litellm_audio_args():
    """
    Requires:
        - PLATFORM_OPENAI_CHAT_ENDPOINT: An OpenAI-compatible endpoint
        - PLATFORM_OPENAI_CHAT_KEY: The API key
    """
    endpoint = os.environ.get("PLATFORM_OPENAI_CHAT_ENDPOINT")
    api_key = os.environ.get("PLATFORM_OPENAI_CHAT_KEY")

    if not endpoint or not api_key:
        pytest.skip("PLATFORM_OPENAI_CHAT_ENDPOINT and PLATFORM_OPENAI_CHAT_KEY must be set")

    model = os.environ.get("PLATFORM_OPENAI_AUDIO_MODEL", "gpt-audio")
    return {
        "model_name": f"openai/{model}",
        "endpoint": endpoint,
        "api_key": api_key,
    }


@pytest.mark.run_only_if_all_tests
async def test_litellm_chat_target_text_completion(sqlite_instance, platform_litellm_args):
    target = LiteLLMChatTarget(**platform_litellm_args)

    user_piece = MessagePiece(
        role="user",
        original_value="Reply with exactly the word: pong",
        original_value_data_type="text",
        conversation_id=str(uuid.uuid4()),
    )

    result = await target.send_prompt_async(message=user_piece.to_message())

    assert result is not None
    assert len(result) >= 1
    text_pieces = [p for p in result[0].message_pieces if p.converted_value_data_type == "text"]
    assert len(text_pieces) >= 1
    assert "pong" in text_pieces[0].converted_value.lower()


@pytest.mark.run_only_if_all_tests
async def test_litellm_chat_target_tool_calling(sqlite_instance, platform_litellm_args):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_stock_price",
                "description": "Get the current stock price for a given ticker symbol",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "The stock ticker symbol, e.g. AAPL"},
                    },
                    "required": ["ticker"],
                },
            },
        },
    ]

    target = LiteLLMChatTarget(
        **platform_litellm_args,
        extra_body_parameters={"tools": tools, "tool_choice": "auto"},
    )

    user_piece = MessagePiece(
        role="user",
        original_value="What's the current stock price for Microsoft (MSFT)?",
        original_value_data_type="text",
        conversation_id=str(uuid.uuid4()),
    )

    result = await target.send_prompt_async(message=user_piece.to_message())

    tool_call_pieces = [p for p in result[0].message_pieces if p.converted_value_data_type == "function_call"]
    assert len(tool_call_pieces) >= 1, "Response should contain at least one tool call"
    tool_call_data = json.loads(tool_call_pieces[0].converted_value)
    assert tool_call_data["function"]["name"] == "get_stock_price"
    assert "msft" in tool_call_data["function"]["arguments"].lower()


@pytest.mark.run_only_if_all_tests
async def test_litellm_chat_target_token_usage_in_metadata(sqlite_instance, platform_litellm_args):
    target = LiteLLMChatTarget(**platform_litellm_args)

    user_piece = MessagePiece(
        role="user",
        original_value="Say hello in one word.",
        original_value_data_type="text",
        conversation_id=str(uuid.uuid4()),
    )

    result = await target.send_prompt_async(message=user_piece.to_message())

    metadata = result[0].message_pieces[0].prompt_metadata
    usage = TokenUsage.from_metadata(metadata)
    assert usage is not None
    assert usage.input_tokens is not None and usage.input_tokens > 0
    assert usage.output_tokens is not None and usage.output_tokens > 0
    assert usage.total_tokens == usage.input_tokens + usage.output_tokens


@pytest.mark.run_only_if_all_tests
async def test_litellm_chat_target_image_input(sqlite_instance, platform_litellm_args):
    """Send a text + image message and verify the vision model returns a text response."""
    target = LiteLLMChatTarget(
        **platform_litellm_args,
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_message_pieces=True,
                input_modalities=frozenset(
                    {frozenset({"text", "image_path"}), frozenset({"text"}), frozenset({"image_path"})}
                ),
            )
        ),
    )

    conv_id = str(uuid.uuid4())
    text_piece = MessagePiece(
        role="user",
        original_value="Describe what this image shows in one short sentence.",
        original_value_data_type="text",
        conversation_id=conv_id,
    )
    image_piece = MessagePiece(
        role="user",
        original_value=str(SAMPLE_IMAGE_FILE),
        original_value_data_type="image_path",
        conversation_id=conv_id,
    )

    result = await target.send_prompt_async(message=Message(message_pieces=[text_piece, image_piece]))

    assert result is not None
    text_pieces = [p for p in result[0].message_pieces if p.converted_value_data_type == "text"]
    assert len(text_pieces) >= 1
    assert text_pieces[0].converted_value.strip()


@pytest.mark.run_only_if_all_tests
async def test_litellm_chat_target_audio_input_output(sqlite_instance, platform_litellm_audio_args):
    """Verify audio output generation and audio input handling against an audio-capable model."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav")

    target = LiteLLMChatTarget(
        **platform_litellm_audio_args,
        audio_response_config=audio_config,
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                input_modalities=frozenset(
                    {frozenset({"text", "audio_path"}), frozenset({"text"}), frozenset({"audio_path"})}
                ),
            )
        ),
    )

    conv_id = str(uuid.uuid4())

    text_piece = MessagePiece(
        role="user",
        original_value="Hello! What's your name?",
        original_value_data_type="text",
        conversation_id=conv_id,
    )
    result1 = await target.send_prompt_async(message=text_piece.to_message())
    audio_pieces1 = [p for p in result1[0].message_pieces if p.converted_value_data_type == "audio_path"]
    assert len(audio_pieces1) >= 1, "First response should contain audio"

    audio_piece = MessagePiece(
        role="user",
        original_value=str(SAMPLE_AUDIO_FILE),
        original_value_data_type="audio_path",
        conversation_id=conv_id,
    )
    result2 = await target.send_prompt_async(message=audio_piece.to_message())
    audio_pieces2 = [p for p in result2[0].message_pieces if p.converted_value_data_type == "audio_path"]
    assert len(audio_pieces2) >= 1, "Second response should contain audio"
