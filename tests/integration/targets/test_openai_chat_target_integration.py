# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration tests for OpenAIChatTarget.

These tests verify:
- Audio input/output functionality using models that support native audio modalities
- Tool calling functionality with function definitions
"""

import json
import os
import uuid

import pytest

from pyrit.common.path import HOME_PATH
from pyrit.models import MessagePiece, TokenUsage
from pyrit.prompt_target import OpenAIChatAudioConfig, OpenAIChatTarget, TargetCapabilities, TargetConfiguration

# Path to sample audio file for testing
SAMPLE_AUDIO_FILE = HOME_PATH / "assets" / "converted_audio.wav"


@pytest.fixture()
def platform_openai_audio_args():
    """
    Fixture for OpenAI platform audio-capable model.

    Requires:
        - PLATFORM_OPENAI_CHAT_ENDPOINT: The OpenAI API endpoint (e.g., https://api.openai.com/v1)
        - PLATFORM_OPENAI_CHAT_KEY: The OpenAI API key
    """
    endpoint = os.environ.get("PLATFORM_OPENAI_CHAT_ENDPOINT")
    api_key = os.environ.get("PLATFORM_OPENAI_CHAT_KEY")

    if not endpoint or not api_key:
        pytest.skip("PLATFORM_OPENAI_CHAT_ENDPOINT and PLATFORM_OPENAI_CHAT_KEY must be set")

    return {
        "endpoint": endpoint,
        "api_key": api_key,
        "model_name": os.environ.get("PLATFORM_OPENAI_AUDIO_MODEL", "gpt-audio"),
    }


def _azure_gpt5_credential():
    """
    Return the auth credential for the Azure GPT-5.4 deployment.

    Uses the API key when ``AZURE_OPENAI_GPT5_4_KEY`` is set; otherwise falls back to an Entra ID
    (Azure AD) bearer-token provider, which both targets accept and auto-wrap.
    """
    key = os.environ.get("AZURE_OPENAI_GPT5_4_KEY")
    if key:
        return key

    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    return get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")


@pytest.fixture()
def azure_gpt5_chat_args():
    """
    Fixture for the Azure OpenAI GPT-5.4 chat deployment (non-audio).

    Requires:
        - AZURE_OPENAI_GPT5_4_ENDPOINT: The Azure OpenAI endpoint (OpenAI-compatible /openai/v1)
        - AZURE_OPENAI_GPT5_4_KEY (optional): The API key; if unset, Entra ID auth is used
    """
    endpoint = os.environ.get("AZURE_OPENAI_GPT5_4_ENDPOINT")

    if not endpoint:
        pytest.skip("AZURE_OPENAI_GPT5_4_ENDPOINT must be set")

    return {
        "endpoint": endpoint,
        "api_key": _azure_gpt5_credential(),
        "model_name": os.environ.get("AZURE_OPENAI_GPT5_4_MODEL", "gpt-5.4"),
    }


# ============================================================================
# Audio Tests
# ============================================================================


async def test_openai_chat_target_audio_multi_turn(sqlite_instance, platform_openai_audio_args):
    """
    Test multi-turn conversation with audio output.

    This test verifies that:
    1. Multiple turns of conversation work with audio output
    2. Conversation history is properly maintained
    3. Audio is generated for each assistant response
    """
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav")

    target = OpenAIChatTarget(
        **platform_openai_audio_args,
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

    # First turn
    user_piece1 = MessagePiece(
        role="user",
        original_value="Hello! What's your name?",
        original_value_data_type="text",
        conversation_id=conv_id,
    )

    result1 = await target.send_prompt_async(message=user_piece1.to_message())
    assert result1 is not None
    assert len(result1) >= 1

    # Verify first response has audio
    audio_pieces1 = [p for p in result1[0].message_pieces if p.converted_value_data_type == "audio_path"]
    assert len(audio_pieces1) >= 1, "First response should contain audio"

    # Second turn - send audio input
    user_piece2 = MessagePiece(
        role="user",
        original_value=str(SAMPLE_AUDIO_FILE),
        original_value_data_type="audio_path",
        conversation_id=conv_id,
    )

    result2 = await target.send_prompt_async(message=user_piece2.to_message())
    assert result2 is not None
    assert len(result2) >= 1

    # Verify second response has audio
    audio_pieces2 = [p for p in result2[0].message_pieces if p.converted_value_data_type == "audio_path"]
    assert len(audio_pieces2) >= 1, "Second response should contain audio"


# ============================================================================
# Tool Calling Tests
# ============================================================================


async def test_openai_chat_target_tool_calling_multiple_tools(sqlite_instance, azure_gpt5_chat_args):
    """
    Test that OpenAIChatTarget can handle multiple tool definitions.

    This test verifies that:
    1. Multiple tools can be defined
    2. The model selects the appropriate tool based on context
    """
    # Define multiple tools
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_current_weather",
                "description": "Get the current weather in a given location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "The city and state"},
                    },
                    "required": ["location"],
                },
            },
        },
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

    target = OpenAIChatTarget(
        **azure_gpt5_chat_args,
        extra_body_parameters={"tools": tools, "tool_choice": "auto"},
    )

    conv_id = str(uuid.uuid4())

    # Send a prompt that should trigger the stock price tool
    user_piece = MessagePiece(
        role="user",
        original_value="What's the current stock price for Microsoft (MSFT)?",
        original_value_data_type="text",
        conversation_id=conv_id,
    )

    result = await target.send_prompt_async(message=user_piece.to_message())
    assert result is not None
    assert len(result) >= 1

    # Find tool call pieces in the response
    tool_call_pieces = [p for p in result[0].message_pieces if p.converted_value_data_type == "function_call"]

    # The model should have returned a tool call for stock price
    assert len(tool_call_pieces) >= 1, "Response should contain at least one tool call"

    # Verify it selected the stock price tool
    tool_call_data = json.loads(tool_call_pieces[0].converted_value)
    assert tool_call_data["function"]["name"] == "get_stock_price"
    assert "msft" in tool_call_data["function"]["arguments"].lower()


# ============================================================================
# Token Usage Metadata Tests
# ============================================================================


async def test_openai_chat_target_token_usage_in_metadata(sqlite_instance, azure_gpt5_chat_args):
    """
    Test that token usage metadata is captured from a real API response.

    This test verifies that:
    1. Token usage is recoverable via ``TokenUsage.from_metadata``
    2. Token counts are positive integers
    3. The total equals input + output
    """
    target = OpenAIChatTarget(**azure_gpt5_chat_args)

    conv_id = str(uuid.uuid4())

    user_piece = MessagePiece(
        role="user",
        original_value="Say hello in one word.",
        original_value_data_type="text",
        conversation_id=conv_id,
    )

    result = await target.send_prompt_async(message=user_piece.to_message())
    assert result is not None
    assert len(result) >= 1

    first_piece = result[0].message_pieces[0]
    usage = TokenUsage.from_metadata(first_piece.prompt_metadata)

    assert usage is not None, "Response should contain token-usage metadata"
    assert usage.input_tokens is not None and usage.input_tokens > 0
    assert usage.output_tokens is not None and usage.output_tokens > 0
    assert usage.total_tokens is not None and usage.total_tokens > 0
    assert usage.total_tokens == usage.input_tokens + usage.output_tokens
