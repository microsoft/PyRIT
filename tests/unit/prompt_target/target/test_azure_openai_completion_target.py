# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from collections.abc import MutableSequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.types.completion import Completion
from openai.types.completion_choice import CompletionChoice
from openai.types.completion_usage import CompletionUsage
from unit.mocks import get_image_message_piece, get_sample_conversations

from pyrit.memory.central_memory import CentralMemory
from pyrit.models import Message, MessagePiece, flatten_to_message_pieces
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
    return flatten_to_message_pieces(conversations)


async def test_azure_completion_validate_request_length(azure_completion_target: OpenAICompletionTarget):
    request = Message(
        message_pieces=[
            MessagePiece(role="user", conversation_id="123", original_value="test"),
            MessagePiece(role="user", conversation_id="123", original_value="test2"),
        ]
    )
    with pytest.raises(
        ValueError,
        match="This target only supports a single message piece.*If your target does support this, set the"
        " custom_configuration parameter accordingly",
    ):
        await azure_completion_target.send_prompt_async(message=request)


async def test_azure_completion_validate_prompt_type(azure_completion_target: OpenAICompletionTarget):
    request = Message(message_pieces=[get_image_message_piece()])
    with pytest.raises(
        ValueError,
        match="This target supports only the following data types.*If your target does support this, set the"
        " custom_configuration parameter accordingly",
    ):
        await azure_completion_target.send_prompt_async(message=request)


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


async def test_completion_target_does_not_detect_truncation(azure_completion_target: OpenAICompletionTarget):
    """A target that does not implement truncation detection inherits the base opt-out."""
    response = MagicMock()
    response.choices = [MagicMock(finish_reason="length")]

    assert azure_completion_target._is_truncated_response(response) is False


@pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter"])
async def test_completion_target_captures_usage_and_finish_reason(
    azure_completion_target: OpenAICompletionTarget,
    sample_conversations: MutableSequence[MessagePiece],
    finish_reason: str,
):
    """The Completions API reports the same usage and finish_reason fields as Chat Completions."""
    response = Completion(
        id="cmpl-1",
        object="text_completion",
        created=0,
        model="gpt-35-turbo",
        choices=[CompletionChoice(finish_reason=finish_reason, index=0, text="hi")],
        usage=CompletionUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )

    message = await azure_completion_target._construct_message_from_response_async(
        response=response, request=sample_conversations[0]
    )

    metadata = message.message_pieces[0].prompt_metadata
    assert metadata["finish_reason"] == finish_reason
    assert metadata["token_usage_input_tokens"] == 11
    assert metadata["token_usage_output_tokens"] == 7
    assert metadata["token_usage_total_tokens"] == 18


async def test_completion_target_captures_finish_reason_per_choice(
    azure_completion_target: OpenAICompletionTarget,
    sample_conversations: MutableSequence[MessagePiece],
):
    """With n>1 each piece is its own choice, so a filter on a later choice must not be hidden."""
    response = Completion(
        id="cmpl-1",
        object="text_completion",
        created=0,
        model="gpt-35-turbo",
        choices=[
            CompletionChoice(finish_reason="stop", index=0, text="allowed"),
            CompletionChoice(finish_reason="content_filter", index=1, text=""),
        ],
        usage=CompletionUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )

    message = await azure_completion_target._construct_message_from_response_async(
        response=response, request=sample_conversations[0]
    )

    assert [piece.prompt_metadata.get("finish_reason") for piece in message.message_pieces] == [
        "stop",
        "content_filter",
    ]
    # Usage is per call, not per choice, so it stays on the first piece only.
    assert message.message_pieces[0].prompt_metadata["token_usage_total_tokens"] == 18
    assert "token_usage_total_tokens" not in message.message_pieces[1].prompt_metadata


async def test_completion_target_clears_pieces_without_a_matching_choice(
    azure_completion_target: OpenAICompletionTarget,
    sample_conversations: MutableSequence[MessagePiece],
):
    """Every piece is cleared, so a piece the provider said nothing about reports nothing."""
    pieces = [sample_conversations[0], sample_conversations[1]]
    for piece in pieces:
        piece.prompt_metadata["finish_reason"] = "caller_supplied"
    response = Completion(
        id="cmpl-1",
        object="text_completion",
        created=0,
        model="gpt-35-turbo",
        choices=[CompletionChoice(finish_reason="stop", index=0, text="allowed")],
        usage=None,
    )

    azure_completion_target._capture_response_metadata(response=response, pieces=pieces)

    assert [piece.prompt_metadata.get("finish_reason") for piece in pieces] == ["stop", None]
