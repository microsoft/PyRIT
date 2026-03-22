# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Integration tests for MiniMaxChatTarget.

These tests verify:
- Basic chat completion with MiniMax API
- Multi-turn conversation support
- JSON mode output

Requirements:
    - MINIMAX_API_KEY: A valid MiniMax API key
"""

import os
import uuid

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.minimax.minimax_chat_target import MiniMaxChatTarget


@pytest.fixture()
def minimax_chat_args():
    """
    Fixture for MiniMax chat model configuration.

    Requires:
        - MINIMAX_API_KEY: The MiniMax API key
    """
    api_key = os.environ.get("MINIMAX_API_KEY")

    if not api_key:
        pytest.skip("MINIMAX_API_KEY must be set for MiniMax integration tests")

    return {
        "api_key": api_key,
        "model_name": "MiniMax-M2.7",
        "endpoint": "https://api.minimax.io/v1",
    }


@pytest.mark.asyncio
async def test_minimax_chat_basic_completion(sqlite_instance, minimax_chat_args):
    """Test basic chat completion with MiniMax."""
    target = MiniMaxChatTarget(**minimax_chat_args, temperature=0.1)

    conversation_id = str(uuid.uuid4())
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id=conversation_id,
                original_value="What is 2 + 2? Reply with just the number.",
                converted_value="What is 2 + 2? Reply with just the number.",
                original_value_data_type="text",
                converted_value_data_type="text",
            )
        ]
    )

    result = await target.send_prompt_async(message=message)

    assert len(result) == 1
    assert len(result[0].message_pieces) == 1
    response_text = result[0].get_value()
    assert "4" in response_text


@pytest.mark.asyncio
async def test_minimax_chat_multi_turn(sqlite_instance, minimax_chat_args):
    """Test multi-turn conversation with MiniMax."""
    target = MiniMaxChatTarget(**minimax_chat_args, temperature=0.1)

    conversation_id = str(uuid.uuid4())

    # First turn
    message1 = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id=conversation_id,
                original_value="My name is Alice. Remember it.",
                converted_value="My name is Alice. Remember it.",
                original_value_data_type="text",
                converted_value_data_type="text",
            )
        ]
    )

    result1 = await target.send_prompt_async(message=message1)
    assert len(result1) == 1

    # Second turn - test that conversation context is maintained
    message2 = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id=conversation_id,
                original_value="What is my name?",
                converted_value="What is my name?",
                original_value_data_type="text",
                converted_value_data_type="text",
            )
        ]
    )

    result2 = await target.send_prompt_async(message=message2)
    assert len(result2) == 1
    assert "Alice" in result2[0].get_value()


@pytest.mark.asyncio
async def test_minimax_chat_json_mode(sqlite_instance, minimax_chat_args):
    """Test JSON mode output with MiniMax."""
    target = MiniMaxChatTarget(**minimax_chat_args, temperature=0.1)

    conversation_id = str(uuid.uuid4())
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id=conversation_id,
                original_value='Return a JSON object with a "color" key set to "blue".',
                converted_value='Return a JSON object with a "color" key set to "blue".',
                original_value_data_type="text",
                converted_value_data_type="text",
                prompt_metadata={"response_format": "json"},
            )
        ]
    )

    result = await target.send_prompt_async(message=message)
    assert len(result) == 1

    import json

    response_text = result[0].get_value()
    parsed = json.loads(response_text)
    assert parsed.get("color") == "blue"
