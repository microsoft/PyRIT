# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from collections.abc import MutableSequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_image_message_piece, get_sample_conversations

from pyrit.memory.central_memory import CentralMemory
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import OpenAICompletionTarget


@pytest.fixture
def completions_response_json() -> dict:
    return {
        "id": "12345678-1a2b-3c4e5f-a123-12345678abcd",
        "object": "text_completion",
        "choices": [
            {
                "index": 0,
                "text": "hi",
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "model": "gpt-35-turbo",
    }


@pytest.fixture
def azure_completion_target(patch_central_database) -> OpenAICompletionTarget:
    return OpenAICompletionTarget(
        model_name="gpt-35-turbo",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )


@pytest.fixture
def sample_conversations() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


@pytest.mark.asyncio
async def test_azure_completion_validate_request_length(azure_completion_target: OpenAICompletionTarget):
    request = Message(
        message_pieces=[
            MessagePiece(role="user", conversation_id="123", original_value="test"),
            MessagePiece(role="user", conversation_id="123", original_value="test2"),
        ]
    )
    with pytest.raises(ValueError, match="This target only supports a single message piece."):
        await azure_completion_target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_azure_completion_validate_prompt_type(azure_completion_target: OpenAICompletionTarget):
    request = Message(message_pieces=[get_image_message_piece()])
    with pytest.raises(ValueError, match="This target only supports text prompt input."):
        await azure_completion_target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_azure_complete_async_return(
    completions_response_json: dict,
    azure_completion_target: OpenAICompletionTarget,
    sample_conversations: MutableSequence[MessagePiece],
):
    message_piece = sample_conversations[0]
    request = Message(message_pieces=[message_piece])

    # Mock SDK response
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.text = "hi"
    mock_response.choices = [mock_choice]

    with patch.object(
        azure_completion_target._async_client.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response
        response: list[Message] = await azure_completion_target.send_prompt_async(message=request)
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].get_value() == "hi"


def test_azure_initialization_with_no_deployment_raises():
    with patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock()):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError):
                OpenAICompletionTarget()


def test_azure_invalid_endpoint_raises():
    with patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock()):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError):
                OpenAICompletionTarget(
                    model_name="gpt-4",
                    endpoint="",
                    api_key="xxxxx",
                )
