# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import MutableSequence
from unittest.mock import MagicMock

import pytest
from unit.mocks import get_audio_message_piece, get_sample_conversations

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import PromptShieldTarget


@pytest.fixture
def audio_message_piece() -> MessagePiece:
    return get_audio_message_piece()


@pytest.fixture
def sample_conversations() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


@pytest.fixture
def promptshield_target(sqlite_instance) -> PromptShieldTarget:
    return PromptShieldTarget(endpoint="mock", api_key="mock")


@pytest.fixture
def sample_delineated_prompt_as_str() -> str:
    sample: str = """
    Mock userPrompt
    <document>
    mock document
    </document>
    """
    return sample


@pytest.fixture
def sample_delineated_prompt_as_dict() -> dict:
    sample: dict = {"userPrompt": "\n    Mock userPrompt\n    ", "documents": ["\n    mock document\n    "]}
    return sample


@pytest.fixture
def sample_conversation_piece(sample_delineated_prompt_as_str: str) -> MessagePiece:
    return MessagePiece(role="user", original_value=sample_delineated_prompt_as_str)


def test_promptshield_init(promptshield_target: PromptShieldTarget):
    assert promptshield_target


@pytest.mark.asyncio
async def test_prompt_shield_validate_request_length(promptshield_target: PromptShieldTarget):
    request = Message(
        message_pieces=[
            MessagePiece(role="user", conversation_id="123", original_value="test1"),
            MessagePiece(role="user", conversation_id="123", original_value="test2"),
        ]
    )
    with pytest.raises(ValueError, match="This target only supports a single message piece."):
        await promptshield_target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_prompt_shield_reject_non_text(
    promptshield_target: PromptShieldTarget, audio_message_piece: MessagePiece
):
    with pytest.raises(ValueError):
        await promptshield_target.send_prompt_async(message=Message([audio_message_piece]))


@pytest.mark.asyncio
async def test_prompt_shield_document_parsing(
    promptshield_target: PromptShieldTarget, sample_delineated_prompt_as_str: str, sample_delineated_prompt_as_dict
):
    result = promptshield_target._input_parser(sample_delineated_prompt_as_str)

    assert result == sample_delineated_prompt_as_dict


@pytest.mark.asyncio
async def test_prompt_shield_response_validation(promptshield_target: PromptShieldTarget):
    # This tests handling both an empty request and an empty response
    promptshield_target._validate_response(request_body={}, response_body={})


def test_api_key_authentication():
    """Test that API key authentication works correctly."""
    target = PromptShieldTarget(endpoint="https://test.endpoint.com", api_key="test_key")

    # Verify target was created successfully with API key
    assert target is not None
    assert target._api_key == "test_key"


def test_token_provider_authentication():
    """Test that token provider (callable) authentication works correctly."""
    token_provider = MagicMock(return_value="test_token")
    target = PromptShieldTarget(endpoint="https://test.endpoint.com", api_key=token_provider)

    # Verify target was created successfully with token provider
    assert target is not None
    assert target._api_key == token_provider
    assert callable(target._api_key)
