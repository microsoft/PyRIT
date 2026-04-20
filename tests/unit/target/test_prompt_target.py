# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import MutableSequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_sample_conversations, openai_chat_response_json_dict

from pyrit.executor.attack.core.attack_strategy import AttackStrategy
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import OpenAIChatTarget


@pytest.fixture
def sample_entries() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


@pytest.fixture
def openai_response_json() -> dict:
    return openai_chat_response_json_dict()


@pytest.fixture
def azure_openai_target(patch_central_database):
    return OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="test",
        api_key="test",
    )


@pytest.fixture
def mock_attack_strategy():
    """Create a mock attack strategy for testing"""
    strategy = MagicMock(spec=AttackStrategy)
    strategy.execute_async = AsyncMock()
    strategy.execute_with_context_async = AsyncMock()
    strategy.get_identifier.return_value = {
        "__type__": "TestAttack",
        "__module__": "pyrit.executor.attack.test_attack",
        "id": str(uuid.uuid4()),
    }
    return strategy


def test_set_system_prompt(azure_openai_target: OpenAIChatTarget, mock_attack_strategy: AttackStrategy):
    azure_openai_target.set_system_prompt(
        system_prompt="system prompt",
        conversation_id="1",
        attack_identifier=mock_attack_strategy.get_identifier(),
        labels={},
    )

    chats = azure_openai_target._memory.get_message_pieces(conversation_id="1")
    assert len(chats) == 1, f"Expected 1 chat, got {len(chats)}"
    assert chats[0].api_role == "system"
    assert chats[0].converted_value == "system prompt"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_set_system_prompt_adds_memory(
    azure_openai_target: OpenAIChatTarget, mock_attack_strategy: AttackStrategy
):
    azure_openai_target.set_system_prompt(
        system_prompt="system prompt",
        conversation_id="1",
        attack_identifier=mock_attack_strategy.get_identifier(),
        labels={},
    )

    chats = azure_openai_target._memory.get_message_pieces(conversation_id="1")
    assert len(chats) == 1, f"Expected 1 chats, got {len(chats)}"
    assert chats[0].api_role == "system"


@pytest.mark.asyncio
async def test_send_prompt_with_system_calls_chat_complete(
    azure_openai_target: OpenAIChatTarget,
    openai_response_json: dict,
    sample_entries: MutableSequence[MessagePiece],
    mock_attack_strategy: AttackStrategy,
):
    # Mock SDK response
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.finish_reason = "stop"
    mock_message = MagicMock()
    mock_message.content = "hi"
    mock_message.audio = None  # Explicitly set to avoid MagicMock auto-creation
    mock_message.tool_calls = None
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]

    with patch.object(
        azure_openai_target._async_client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response

        azure_openai_target.set_system_prompt(
            system_prompt="system prompt",
            conversation_id="1",
            attack_identifier=mock_attack_strategy.get_identifier(),
            labels={},
        )

        request = sample_entries[0]
        request.converted_value = "hi, I am a victim chatbot, how can I help?"
        request.conversation_id = "1"

        await azure_openai_target.send_prompt_async(message=Message(message_pieces=[request]))

        mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_send_prompt_async_with_delay(
    azure_openai_target: OpenAIChatTarget,
    openai_response_json: dict,
    sample_entries: MutableSequence[MessagePiece],
):
    azure_openai_target._max_requests_per_minute = 10

    # Mock SDK response
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.finish_reason = "stop"
    mock_message = MagicMock()
    mock_message.content = "hi"
    mock_message.audio = None  # Explicitly set to avoid MagicMock auto-creation
    mock_message.tool_calls = None
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]

    with patch.object(
        azure_openai_target._async_client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        with patch("asyncio.sleep") as mock_sleep:
            mock_create.return_value = mock_response

            request = sample_entries[0]
            request.converted_value = "hi, I am a victim chatbot, how can I help?"

            await azure_openai_target.send_prompt_async(message=Message(message_pieces=[request]))

            mock_create.assert_called_once()
            mock_sleep.assert_called_once_with(6)  # 60/max_requests_per_minute
