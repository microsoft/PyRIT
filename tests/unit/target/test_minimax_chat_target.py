# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError, BadRequestError, RateLimitError
from openai.types.chat import ChatCompletion

from pyrit.exceptions.exception_classes import EmptyResponseException, PyritException, RateLimitException
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.models.json_response_config import _JsonResponseConfig
from pyrit.prompt_target import PromptChatTarget
from pyrit.prompt_target.minimax.minimax_chat_target import MiniMaxChatTarget


def create_mock_completion(content: str = "hi", finish_reason: str = "stop"):
    """Helper to create a mock OpenAI-compatible completion response."""
    mock_completion = MagicMock(spec=ChatCompletion)
    mock_completion.choices = [MagicMock()]
    mock_completion.choices[0].finish_reason = finish_reason
    mock_completion.choices[0].message.content = content
    mock_completion.choices[0].message.tool_calls = None
    mock_completion.model_dump_json.return_value = json.dumps(
        {"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]}
    )
    mock_completion.usage = None
    return mock_completion


@pytest.fixture
def dummy_text_message_piece() -> MessagePiece:
    return MessagePiece(
        role="user",
        conversation_id="dummy_convo",
        original_value="dummy text",
        converted_value="dummy text",
        original_value_data_type="text",
        converted_value_data_type="text",
    )


@pytest.fixture
def target(patch_central_database) -> MiniMaxChatTarget:
    return MiniMaxChatTarget(
        api_key="mock-minimax-api-key",
        model_name="MiniMax-M2.7",
        endpoint="https://api.minimax.io/v1",
    )


# ============================================================================
# Initialization Tests
# ============================================================================


def test_init_with_no_api_key_raises():
    """Test that initialization without API key raises ValueError."""
    with patch.dict(os.environ, {}, clear=True), pytest.raises(ValueError):
        MiniMaxChatTarget()


def test_init_with_api_key_from_env(patch_central_database):
    """Test initialization with API key from environment variable."""
    with patch.dict(os.environ, {"MINIMAX_API_KEY": "env-api-key"}, clear=False):
        target = MiniMaxChatTarget()
        assert target._api_key == "env-api-key"
        assert target._model_name == "MiniMax-M2.7"  # default


def test_init_with_explicit_params(patch_central_database):
    """Test initialization with explicit parameters."""
    target = MiniMaxChatTarget(
        api_key="test-key",
        model_name="MiniMax-M2.5",
        endpoint="https://custom.minimax.io/v1",
        temperature=0.5,
        top_p=0.9,
        max_completion_tokens=1024,
    )
    assert target._api_key == "test-key"
    assert target._model_name == "MiniMax-M2.5"
    assert target._endpoint == "https://custom.minimax.io/v1"
    assert target._temperature == 0.5
    assert target._top_p == 0.9
    assert target._max_completion_tokens == 1024


def test_init_default_endpoint(patch_central_database):
    """Test that default endpoint is set when not provided."""
    target = MiniMaxChatTarget(api_key="test-key")
    assert target._endpoint == "https://api.minimax.io/v1"


def test_init_default_model(patch_central_database):
    """Test that default model is set when not provided."""
    target = MiniMaxChatTarget(api_key="test-key")
    assert target._model_name == "MiniMax-M2.7"


def test_inheritance_from_prompt_chat_target(target: MiniMaxChatTarget):
    """Test that MiniMaxChatTarget properly inherits from PromptChatTarget."""
    assert isinstance(target, PromptChatTarget)


# ============================================================================
# Temperature Validation Tests
# ============================================================================


def test_invalid_temperature_raises(patch_central_database):
    """Test that temperature outside [0, 2] raises PyritException."""
    with pytest.raises(PyritException, match="temperature must be between 0 and 2"):
        MiniMaxChatTarget(api_key="test-key", temperature=-0.1)

    with pytest.raises(PyritException, match="temperature must be between 0 and 2"):
        MiniMaxChatTarget(api_key="test-key", temperature=2.1)


def test_temperature_clamped_to_minimax_max(patch_central_database, caplog):
    """Test that temperature > 1.0 is clamped to 1.0 for MiniMax."""
    import logging

    with caplog.at_level(logging.WARNING):
        target = MiniMaxChatTarget(api_key="test-key", temperature=1.5)

    assert target._temperature == 1.0
    assert any("Clamping" in record.message for record in caplog.records)


def test_temperature_zero_accepted(patch_central_database):
    """Test that temperature=0 is accepted."""
    target = MiniMaxChatTarget(api_key="test-key", temperature=0.0)
    assert target._temperature == 0.0


def test_temperature_one_accepted(patch_central_database):
    """Test that temperature=1.0 is accepted without clamping."""
    target = MiniMaxChatTarget(api_key="test-key", temperature=1.0)
    assert target._temperature == 1.0


# ============================================================================
# Top-p Validation Tests
# ============================================================================


def test_invalid_top_p_raises(patch_central_database):
    """Test that invalid top_p values raise PyritException."""
    with pytest.raises(PyritException, match="top_p must be between 0 and 1"):
        MiniMaxChatTarget(api_key="test-key", top_p=-0.1)

    with pytest.raises(PyritException, match="top_p must be between 0 and 1"):
        MiniMaxChatTarget(api_key="test-key", top_p=1.1)


# ============================================================================
# Request Body Construction Tests
# ============================================================================


def test_construct_request_body_minimal(target: MiniMaxChatTarget, dummy_text_message_piece: MessagePiece):
    """Test minimal request body construction."""
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)

    body = target._construct_request_body(conversation=[request], json_config=jrc)

    assert body["model"] == "MiniMax-M2.7"
    assert body["messages"][0]["content"] == "dummy text"
    assert body["stream"] is False
    assert "temperature" not in body
    assert "top_p" not in body
    assert "max_tokens" not in body
    assert "response_format" not in body


def test_construct_request_body_with_params(patch_central_database, dummy_text_message_piece: MessagePiece):
    """Test request body includes configured parameters."""
    target = MiniMaxChatTarget(
        api_key="test-key",
        temperature=0.7,
        top_p=0.9,
        max_completion_tokens=512,
    )

    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)

    body = target._construct_request_body(conversation=[request], json_config=jrc)

    assert body["temperature"] == 0.7
    assert body["top_p"] == 0.9
    assert body["max_tokens"] == 512


def test_construct_request_body_json_mode(target: MiniMaxChatTarget, dummy_text_message_piece: MessagePiece):
    """Test request body with JSON response format."""
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata={"response_format": "json"})

    body = target._construct_request_body(conversation=[request], json_config=jrc)

    assert body["response_format"] == {"type": "json_object"}


# ============================================================================
# Chat Message Building Tests
# ============================================================================


def test_build_chat_messages_single(target: MiniMaxChatTarget, dummy_text_message_piece: MessagePiece):
    """Test building chat messages from a single message."""
    messages = target._build_chat_messages([Message(message_pieces=[dummy_text_message_piece])])
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "dummy text"


def test_build_chat_messages_multi_turn(target: MiniMaxChatTarget):
    """Test building chat messages from a multi-turn conversation."""
    conversation = [
        Message(
            message_pieces=[
                MessagePiece(
                    role="system",
                    conversation_id="conv1",
                    original_value="You are a helpful assistant.",
                    converted_value="You are a helpful assistant.",
                    converted_value_data_type="text",
                )
            ]
        ),
        Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    conversation_id="conv1",
                    original_value="Hello",
                    converted_value="Hello",
                    converted_value_data_type="text",
                )
            ]
        ),
        Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    conversation_id="conv1",
                    original_value="Hi there!",
                    converted_value="Hi there!",
                    converted_value_data_type="text",
                )
            ]
        ),
    ]

    messages = target._build_chat_messages(conversation)
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"


def test_build_chat_messages_rejects_multi_piece(target: MiniMaxChatTarget):
    """Test that multi-piece messages are rejected."""
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="conv1",
                original_value="text",
                converted_value="text",
                converted_value_data_type="text",
            ),
            MessagePiece(
                role="user",
                conversation_id="conv1",
                original_value="more text",
                converted_value="more text",
                converted_value_data_type="text",
            ),
        ]
    )

    with pytest.raises(ValueError, match="single-piece text messages"):
        target._build_chat_messages([message])


def test_build_chat_messages_rejects_non_text(target: MiniMaxChatTarget):
    """Test that non-text data types are rejected."""
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="conv1",
                original_value="/path/to/image.jpg",
                converted_value="/path/to/image.jpg",
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
            )
        ]
    )

    with pytest.raises(ValueError, match="only supports text"):
        target._build_chat_messages([message])


# ============================================================================
# Think Tag Stripping Tests
# ============================================================================


def test_strip_thinking_tags_with_tags():
    """Test stripping <think> tags from response."""
    content = "<think>Let me think about this...</think>The answer is 42."
    result = MiniMaxChatTarget._strip_thinking_tags(content)
    assert result == "The answer is 42."


def test_strip_thinking_tags_without_tags():
    """Test that content without think tags is unchanged."""
    content = "The answer is 42."
    result = MiniMaxChatTarget._strip_thinking_tags(content)
    assert result == "The answer is 42."


def test_strip_thinking_tags_multiline():
    """Test stripping multiline think tags."""
    content = "<think>\nStep 1: Consider this\nStep 2: Do that\n</think>\nFinal answer."
    result = MiniMaxChatTarget._strip_thinking_tags(content)
    assert result == "Final answer."


def test_strip_thinking_tags_empty_think():
    """Test stripping empty think tags."""
    content = "<think></think>Response here."
    result = MiniMaxChatTarget._strip_thinking_tags(content)
    assert result == "Response here."


# ============================================================================
# Send Prompt Tests
# ============================================================================


@pytest.mark.asyncio
async def test_send_prompt_async_success(target: MiniMaxChatTarget):
    """Test successful prompt sending."""
    mock_completion = create_mock_completion(content="Hello from MiniMax!")
    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="test-conv",
                original_value="Hello",
                converted_value="Hello",
                original_value_data_type="text",
                converted_value_data_type="text",
            )
        ]
    )

    result = await target.send_prompt_async(message=message)

    assert len(result) == 1
    assert len(result[0].message_pieces) == 1
    assert result[0].get_value() == "Hello from MiniMax!"


@pytest.mark.asyncio
async def test_send_prompt_async_strips_thinking_tags(target: MiniMaxChatTarget):
    """Test that thinking tags are stripped from response."""
    mock_completion = create_mock_completion(
        content="<think>Internal reasoning here</think>The actual answer."
    )
    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="test-conv",
                original_value="Question",
                converted_value="Question",
                original_value_data_type="text",
                converted_value_data_type="text",
            )
        ]
    )

    result = await target.send_prompt_async(message=message)

    assert result[0].get_value() == "The actual answer."


@pytest.mark.asyncio
async def test_send_prompt_async_empty_response(target: MiniMaxChatTarget):
    """Test that empty response raises EmptyResponseException."""
    mock_completion = create_mock_completion(content="")
    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    target._memory = MagicMock(MemoryInterface)

    message = Message(
        message_pieces=[MessagePiece(role="user", conversation_id="test", original_value="Hello")]
    )

    with pytest.raises(EmptyResponseException):
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_rate_limit(target: MiniMaxChatTarget):
    """Test that RateLimitError raises RateLimitException."""
    mock_request = httpx.Request("POST", "https://api.minimax.io/v1/chat/completions")
    mock_response = httpx.Response(429, text="Rate Limit Reached", request=mock_request)
    side_effect = RateLimitError("Rate Limit Reached", response=mock_response, body=None)

    target._async_client.chat.completions.create = AsyncMock(side_effect=side_effect)

    message = Message(
        message_pieces=[MessagePiece(role="user", conversation_id="test", original_value="Hello")]
    )

    with pytest.raises(RateLimitException):
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_bad_request(target: MiniMaxChatTarget):
    """Test that BadRequestError is handled and returns error response."""
    mock_request = httpx.Request("POST", "https://api.minimax.io/v1/chat/completions")
    error_body = {"error": {"message": "Invalid request", "code": "bad_request"}}
    mock_response = httpx.Response(400, text=json.dumps(error_body), request=mock_request)
    side_effect = BadRequestError("Bad Request", response=mock_response, body=error_body)

    target._async_client.chat.completions.create = AsyncMock(side_effect=side_effect)

    message = Message(
        message_pieces=[MessagePiece(role="user", conversation_id="test", original_value="Hello")]
    )

    # Non-content-filter BadRequestError should be re-raised
    with pytest.raises(Exception):  # noqa: B017
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_api_status_error_429(target: MiniMaxChatTarget):
    """Test that APIStatusError with 429 raises RateLimitException."""
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Too many requests"
    mock_response.headers = {}

    api_error = APIStatusError("Too many requests", response=mock_response, body={})
    api_error.status_code = 429

    target._async_client.chat.completions.create = AsyncMock(side_effect=api_error)

    message = Message(
        message_pieces=[MessagePiece(role="user", conversation_id="test", original_value="Hello")]
    )

    with pytest.raises(RateLimitException):
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_api_status_error_500(target: MiniMaxChatTarget):
    """Test that APIStatusError with 500 is re-raised."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.headers = {}

    api_error = APIStatusError("Internal Server Error", response=mock_response, body={})
    api_error.status_code = 500

    target._async_client.chat.completions.create = AsyncMock(side_effect=api_error)

    message = Message(
        message_pieces=[MessagePiece(role="user", conversation_id="test", original_value="Hello")]
    )

    with pytest.raises(APIStatusError):
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_unknown_finish_reason(target: MiniMaxChatTarget):
    """Test that unknown finish_reason raises PyritException."""
    mock_completion = create_mock_completion(content="test", finish_reason="unexpected_reason")
    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    target._memory = MagicMock(MemoryInterface)

    message = Message(
        message_pieces=[MessagePiece(role="user", conversation_id="test", original_value="Hello")]
    )

    with pytest.raises(PyritException, match="Unknown finish_reason"):
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_no_choices(target: MiniMaxChatTarget):
    """Test that response with no choices raises PyritException."""
    mock_completion = create_mock_completion(content="test")
    mock_completion.choices = []
    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)
    target._memory = MagicMock(MemoryInterface)

    message = Message(
        message_pieces=[MessagePiece(role="user", conversation_id="test", original_value="Hello")]
    )

    with pytest.raises(PyritException, match="No choices"):
        await target.send_prompt_async(message=message)


# ============================================================================
# Token Usage Tests
# ============================================================================


@pytest.mark.asyncio
async def test_send_prompt_captures_token_usage(target: MiniMaxChatTarget):
    """Test that token usage metadata is captured from API response."""
    mock_completion = create_mock_completion(content="Response with tokens")
    mock_completion.model = "MiniMax-M2.7"
    mock_completion.usage = MagicMock()
    mock_completion.usage.prompt_tokens = 15
    mock_completion.usage.completion_tokens = 25
    mock_completion.usage.total_tokens = 40

    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    message = Message(
        message_pieces=[MessagePiece(role="user", conversation_id="test", original_value="Hello")]
    )

    result = await target.send_prompt_async(message=message)
    piece = result[0].message_pieces[0]

    assert piece.prompt_metadata["token_usage_model_name"] == "MiniMax-M2.7"
    assert piece.prompt_metadata["token_usage_prompt_tokens"] == 15
    assert piece.prompt_metadata["token_usage_completion_tokens"] == 25
    assert piece.prompt_metadata["token_usage_total_tokens"] == 40


@pytest.mark.asyncio
async def test_send_prompt_no_usage_no_metadata(target: MiniMaxChatTarget):
    """Test that no token metadata is added when response has no usage."""
    mock_completion = create_mock_completion(content="Response")
    mock_completion.usage = None

    target._async_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    message = Message(
        message_pieces=[MessagePiece(role="user", conversation_id="test", original_value="Hello")]
    )

    result = await target.send_prompt_async(message=message)
    piece = result[0].message_pieces[0]

    assert "token_usage_model_name" not in piece.prompt_metadata


# ============================================================================
# Identifier Tests
# ============================================================================


def test_get_identifier_includes_class_name(target: MiniMaxChatTarget):
    """Test that identifier includes correct class name."""
    identifier = target.get_identifier()
    assert identifier.class_name == "MiniMaxChatTarget"


def test_get_identifier_includes_endpoint(target: MiniMaxChatTarget):
    """Test that identifier includes endpoint."""
    identifier = target.get_identifier()
    assert identifier.params["endpoint"] == "https://api.minimax.io/v1"


def test_get_identifier_includes_model(target: MiniMaxChatTarget):
    """Test that identifier includes model name."""
    identifier = target.get_identifier()
    assert identifier.params["model_name"] == "MiniMax-M2.7"


def test_get_identifier_includes_temperature(patch_central_database):
    """Test that identifier includes temperature when set."""
    target = MiniMaxChatTarget(api_key="test-key", temperature=0.7)
    identifier = target.get_identifier()
    assert identifier.params["temperature"] == 0.7


# ============================================================================
# Response Format Tests
# ============================================================================


def test_build_response_format_disabled(target: MiniMaxChatTarget):
    """Test that response format is None when not enabled."""
    jrc = _JsonResponseConfig.from_metadata(metadata=None)
    result = target._build_response_format(jrc)
    assert result is None


def test_build_response_format_json(target: MiniMaxChatTarget):
    """Test that response format is json_object when enabled."""
    jrc = _JsonResponseConfig.from_metadata(metadata={"response_format": "json"})
    result = target._build_response_format(jrc)
    assert result == {"type": "json_object"}


def test_build_response_format_json_schema_falls_back(target: MiniMaxChatTarget):
    """Test that json_schema falls back to json_object for MiniMax."""
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    jrc = _JsonResponseConfig.from_metadata(
        metadata={"response_format": "json", "json_schema": schema}
    )
    # MiniMax doesn't support json_schema, always returns json_object
    result = target._build_response_format(jrc)
    assert result == {"type": "json_object"}
