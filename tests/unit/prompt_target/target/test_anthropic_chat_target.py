# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pyrit.exceptions import EmptyResponseException, PyritException
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.anthropic.anthropic_chat_target import AnthropicChatTarget


def make_anthropic_response(text: str = "hello", stop_reason: str = "end_turn"):
    """Helper to build a mock Anthropic API response."""
    block = MagicMock()
    block.type = "text"
    block.text = text

    usage = MagicMock()
    usage.input_tokens = 10
    usage.output_tokens = 20

    response = MagicMock()
    response.content = [block]
    response.stop_reason = stop_reason
    response.usage = usage
    return response


def make_message_piece(text: str = "hello") -> MessagePiece:
    return MessagePiece(
        role="user",
        conversation_id="test-convo",
        original_value=text,
        converted_value=text,
        original_value_data_type="text",
        converted_value_data_type="text",
        sequence=1,
    )


def make_conversation(text: str = "hello") -> list[Message]:
    return [Message(message_pieces=[make_message_piece(text)])]


@pytest.fixture
def target(patch_central_database):
    return AnthropicChatTarget(api_key="dummy-key", model_name="claude-sonnet-4-6")


# --- init tests ---

def test_init_missing_api_key_raises(patch_central_database):
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="API key"):
            AnthropicChatTarget()


def test_init_with_env_key(patch_central_database):
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "env-key"}):
        t = AnthropicChatTarget()
        assert t._api_key == "env-key"


def test_init_explicit_key_overrides_env(patch_central_database):
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "env-key"}):
        t = AnthropicChatTarget(api_key="explicit-key")
        assert t._api_key == "explicit-key"


def test_init_default_model(patch_central_database):
    t = AnthropicChatTarget(api_key="dummy")
    assert t._model_name == "claude-sonnet-4-6"


# --- _validate_response tests ---

def test_validate_response_valid(target):
    response = make_anthropic_response()
    target._validate_response(response=response, request=make_message_piece())


def test_validate_response_empty_content_raises(target):
    response = make_anthropic_response()
    response.content = []
    with pytest.raises(EmptyResponseException):
        target._validate_response(response=response, request=make_message_piece())


def test_validate_response_bad_stop_reason_raises(target):
    response = make_anthropic_response(stop_reason="error")
    with pytest.raises(PyritException, match="stop_reason"):
        target._validate_response(response=response, request=make_message_piece())


def test_validate_response_max_tokens_ok(target):
    response = make_anthropic_response(stop_reason="max_tokens")
    target._validate_response(response=response, request=make_message_piece())


# --- _construct_message_from_response_async tests ---

@pytest.mark.asyncio
async def test_construct_message_returns_text(target):
    response = make_anthropic_response(text="hi there")
    piece = make_message_piece()
    result = await target._construct_message_from_response_async(response=response, request=piece)
    assert result.message_pieces[0].converted_value == "hi there"


@pytest.mark.asyncio
async def test_construct_message_stores_token_usage(target):
    response = make_anthropic_response()
    piece = make_message_piece()
    result = await target._construct_message_from_response_async(response=response, request=piece)
    metadata = result.message_pieces[0].prompt_metadata
    assert metadata["token_usage_prompt_tokens"] == 10
    assert metadata["token_usage_completion_tokens"] == 20
    assert metadata["token_usage_total_tokens"] == 30


@pytest.mark.asyncio
async def test_construct_message_no_text_blocks_raises(target):
    response = make_anthropic_response()
    response.content[0].type = "tool_use"  # not text
    piece = make_message_piece()
    with pytest.raises(EmptyResponseException):
        await target._construct_message_from_response_async(response=response, request=piece)


# --- _send_prompt_to_target_async tests ---

@pytest.mark.asyncio
async def test_send_prompt_returns_response(target):
    mock_response = make_anthropic_response(text="Claude here!")
    target._client.messages.create = AsyncMock(return_value=mock_response)

    conversation = make_conversation("say hi")
    result = await target._send_prompt_to_target_async(normalized_conversation=conversation)

    assert len(result) == 1
    assert result[0].message_pieces[0].converted_value == "Claude here!"


@pytest.mark.asyncio
async def test_send_prompt_calls_api_with_correct_model(target):
    mock_response = make_anthropic_response()
    target._client.messages.create = AsyncMock(return_value=mock_response)

    await target._send_prompt_to_target_async(normalized_conversation=make_conversation())
    call_kwargs = target._client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_send_prompt_api_error_raises_pyrit_exception(target):
    import anthropic
    target._client.messages.create = AsyncMock(
        side_effect=anthropic.APIError(message="boom", request=MagicMock(), body=None)
    )
    with pytest.raises(PyritException, match="Anthropic API error"):
        await target._send_prompt_to_target_async(normalized_conversation=make_conversation())
