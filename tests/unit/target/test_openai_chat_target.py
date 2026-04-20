# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import json
import logging
import os
from tempfile import NamedTemporaryFile
from typing import MutableSequence
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError, BadRequestError, ContentFilterFinishReasonError, RateLimitError
from openai.types.chat import ChatCompletion
from unit.mocks import (
    get_image_message_piece,
    get_sample_conversations,
    openai_chat_response_json_dict,
)

from pyrit.exceptions.exception_classes import (
    EmptyResponseException,
    PyritException,
    RateLimitException,
)
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.models.json_response_config import _JsonResponseConfig
from pyrit.prompt_target import (
    OpenAIChatAudioConfig,
    OpenAIChatTarget,
    OpenAIResponseTarget,
    PromptChatTarget,
)


def fake_construct_response_from_request(request, response_text_pieces):
    return {"dummy": True, "request": request, "response": response_text_pieces}


def create_mock_completion(content: str = "hi", finish_reason: str = "stop"):
    """Helper to create a mock OpenAI completion response"""
    mock_completion = MagicMock(spec=ChatCompletion)
    mock_completion.choices = [MagicMock()]
    mock_completion.choices[0].finish_reason = finish_reason
    mock_completion.choices[0].message.content = content
    mock_completion.choices[0].message.audio = None
    mock_completion.choices[0].message.tool_calls = None
    mock_completion.model_dump_json.return_value = json.dumps(
        {"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]}
    )
    return mock_completion


@pytest.fixture
def sample_conversations() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


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
def target(patch_central_database) -> OpenAIChatTarget:
    return OpenAIChatTarget(
        model_name="gpt-o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )


@pytest.fixture
def openai_response_json() -> dict:
    return openai_chat_response_json_dict()


def test_init_with_no_deployment_var_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError):
            OpenAIChatTarget()


def test_init_with_no_endpoint_uri_var_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError):
            OpenAIChatTarget(
                model_name="gpt-4",
                endpoint="",
                api_key="xxxxx",
            )


def test_init_with_no_additional_request_headers_var_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError):
            OpenAIChatTarget(model_name="gpt-4", endpoint="", api_key="xxxxx", headers="")


def test_init_is_json_supported_defaults_to_true(patch_central_database):
    target = OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )
    assert target.is_json_response_supported() is True


def test_init_is_json_supported_can_be_set_to_false(patch_central_database):
    target = OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        is_json_supported=False,
    )
    assert target.is_json_response_supported() is False


def test_init_is_json_supported_can_be_set_to_true(patch_central_database):
    target = OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        is_json_supported=True,
    )
    assert target.is_json_response_supported() is True


@pytest.mark.asyncio()
async def test_build_chat_messages_for_multi_modal(target: OpenAIChatTarget):
    image_request = get_image_message_piece()
    entries = [
        Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    converted_value_data_type="text",
                    original_value="Hello",
                    conversation_id=image_request.conversation_id,
                ),
                image_request,
            ]
        )
    ]
    with patch(
        "pyrit.common.data_url_converter.convert_local_image_to_data_url",
        return_value="data:image/jpeg;base64,encoded_string",
    ):
        messages = await target._build_chat_messages_for_multi_modal_async(entries)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"][0]["type"] == "text"  # type: ignore
    assert messages[0]["content"][1]["type"] == "image_url"  # type: ignore

    os.remove(image_request.original_value)


@pytest.mark.asyncio
async def test_build_chat_messages_for_multi_modal_with_unsupported_data_types(target: OpenAIChatTarget):
    # Use video_path which is truly not supported for multimodal chat
    entry = get_image_message_piece()
    entry.converted_value_data_type = "video_path"

    with pytest.raises(ValueError) as excinfo:
        await target._build_chat_messages_for_multi_modal_async([Message(message_pieces=[entry])])
    assert "Multimodal data type video_path is not yet supported." in str(excinfo.value)


@pytest.mark.asyncio
async def test_construct_request_body_includes_extra_body_params(
    patch_central_database, dummy_text_message_piece: MessagePiece
):
    target = OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        extra_body_parameters={"key": "value"},
    )

    request = Message(message_pieces=[dummy_text_message_piece])

    jrc = _JsonResponseConfig.from_metadata(metadata=None)
    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert body["key"] == "value"


@pytest.mark.asyncio
async def test_construct_request_body_json_object(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata={"response_format": "json"})

    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert body["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_construct_request_body_json_schema(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    schema_obj = {"type": "object", "properties": {"name": {"type": "string"}}}
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata={"response_format": "json", "json_schema": schema_obj})

    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "CustomSchema", "schema": schema_obj, "strict": True},
    }


@pytest.mark.asyncio
async def test_construct_request_body_json_schema_optional_params(
    target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece
):
    schema_obj = {"type": "object", "properties": {"name": {"type": "string"}}}
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(
        metadata={
            "response_format": "json",
            "json_schema": schema_obj,
            "json_schema_name": "MySchema",
            "json_schema_strict": False,
        }
    )

    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "MySchema", "schema": schema_obj, "strict": False},
    }


@pytest.mark.asyncio
async def test_construct_request_body_removes_empty_values(
    target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece
):
    request = Message(message_pieces=[dummy_text_message_piece])

    jrc = _JsonResponseConfig.from_metadata(metadata=None)
    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert "max_completion_tokens" not in body
    assert "max_tokens" not in body
    assert "temperature" not in body
    assert "top_p" not in body
    assert "frequency_penalty" not in body
    assert "presence_penalty" not in body
    assert "response_format" not in body


@pytest.mark.asyncio
async def test_construct_request_body_serializes_text_message(
    target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece
):
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)

    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert body["messages"][0]["content"] == "dummy text", (
        "Text messages are serialized in a simple way that's more broadly supported"
    )


@pytest.mark.asyncio
async def test_construct_request_body_serializes_complex_message(
    target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece
):
    image_piece = get_image_message_piece()
    image_piece.conversation_id = dummy_text_message_piece.conversation_id  # Match conversation IDs
    request = Message(message_pieces=[dummy_text_message_piece, image_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)

    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    messages = body["messages"][0]["content"]
    assert len(messages) == 2, "Complex messages are serialized as a list"
    assert messages[0]["type"] == "text", "Text messages are serialized properly when multi-modal"
    assert messages[1]["type"] == "image_url", "Image messages are serialized properly when multi-modal"


@pytest.mark.asyncio
async def test_send_prompt_async_empty_response_adds_to_memory(openai_response_json: dict, target: OpenAIChatTarget):
    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = []
    mock_memory.add_message_to_memory = AsyncMock()

    target._memory = mock_memory

    with NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
        tmp_file_name = tmp_file.name
    assert os.path.exists(tmp_file_name)
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="12345679",
                original_value="hello",
                converted_value="hello",
                original_value_data_type="text",
                converted_value_data_type="text",
                prompt_target_identifier={"target": "target-identifier"},
                attack_identifier={"test": "test"},
                labels={"test": "test"},
            ),
            MessagePiece(
                role="user",
                conversation_id="12345679",
                original_value=tmp_file_name,
                converted_value=tmp_file_name,
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                prompt_target_identifier={"target": "target-identifier"},
                attack_identifier={"test": "test"},
                labels={"test": "test"},
            ),
        ]
    )
    # Make assistant response empty
    with patch(
        "pyrit.common.data_url_converter.convert_local_image_to_data_url",
        return_value="data:image/jpeg;base64,encoded_string",
    ):
        # Mock the OpenAI SDK client to return empty content
        mock_completion = create_mock_completion(content="")
        target._async_client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
            return_value=mock_completion
        )
        target._memory = MagicMock(MemoryInterface)

        with pytest.raises(EmptyResponseException):
            await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_rate_limit_exception_adds_to_memory(
    target: OpenAIChatTarget,
):
    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = []
    mock_memory.add_message_to_memory = AsyncMock()

    target._memory = mock_memory

    # Create proper mock request and response for RateLimitError
    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    mock_response = httpx.Response(429, text="Rate Limit Reached", request=mock_request)
    side_effect = RateLimitError("Rate Limit Reached", response=mock_response, body=None)

    # Mock the OpenAI SDK client method
    target._async_client.chat.completions.create = AsyncMock(side_effect=side_effect)  # type: ignore[method-assign]

    message = Message(message_pieces=[MessagePiece(role="user", conversation_id="123", original_value="Hello")])

    with pytest.raises(RateLimitException):
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_bad_request_error_adds_to_memory(target: OpenAIChatTarget):
    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = []
    mock_memory.add_message_to_memory = AsyncMock()

    target._memory = mock_memory

    message = Message(message_pieces=[MessagePiece(role="user", conversation_id="123", original_value="Hello")])

    # Create proper mock request and response for BadRequestError (without content_filter)
    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    mock_response = httpx.Response(400, text="Some error message", request=mock_request)
    side_effect = BadRequestError("Bad Request", response=mock_response, body="Some error message")

    # Mock the OpenAI SDK client method
    target._async_client.chat.completions.create = AsyncMock(side_effect=side_effect)  # type: ignore[method-assign]

    # Non-content-filter BadRequestError should be re-raised
    with pytest.raises(Exception):  # Will raise since handle_bad_request_exception re-raises non-content-filter errors
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async(openai_response_json: dict, target: OpenAIChatTarget):
    with NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
        tmp_file_name = tmp_file.name
    assert os.path.exists(tmp_file_name)
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="12345679",
                original_value="hello",
                converted_value="hello",
                original_value_data_type="text",
                converted_value_data_type="text",
                prompt_target_identifier={"target": "target-identifier"},
                attack_identifier={"test": "test"},
                labels={"test": "test"},
            ),
            MessagePiece(
                role="user",
                conversation_id="12345679",
                original_value=tmp_file_name,
                converted_value=tmp_file_name,
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                prompt_target_identifier={"target": "target-identifier"},
                attack_identifier={"test": "test"},
                labels={"test": "test"},
            ),
        ]
    )
    with patch(
        "pyrit.common.data_url_converter.convert_local_image_to_data_url",
        return_value="data:image/jpeg;base64,encoded_string",
    ):
        # Mock the OpenAI SDK client to return a completion
        mock_completion = create_mock_completion(content="hi")
        target._async_client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
            return_value=mock_completion
        )

        response: list[Message] = await target.send_prompt_async(message=message)
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].get_value() == "hi"
    os.remove(tmp_file_name)


@pytest.mark.asyncio
async def test_send_prompt_async_empty_response_retries(openai_response_json: dict, target: OpenAIChatTarget):
    with NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
        tmp_file_name = tmp_file.name
    assert os.path.exists(tmp_file_name)
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="12345679",
                original_value="hello",
                converted_value="hello",
                original_value_data_type="text",
                converted_value_data_type="text",
                prompt_target_identifier={"target": "target-identifier"},
                attack_identifier={"test": "test"},
                labels={"test": "test"},
            ),
            MessagePiece(
                role="user",
                conversation_id="12345679",
                original_value=tmp_file_name,
                converted_value=tmp_file_name,
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                prompt_target_identifier={"target": "target-identifier"},
                attack_identifier={"test": "test"},
                labels={"test": "test"},
            ),
        ]
    )
    # Make assistant response empty
    with patch(
        "pyrit.common.data_url_converter.convert_local_image_to_data_url",
        return_value="data:image/jpeg;base64,encoded_string",
    ):
        # Mock the OpenAI SDK client to return empty content
        mock_completion = create_mock_completion(content="")
        target._async_client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
            return_value=mock_completion
        )
        target._memory = MagicMock(MemoryInterface)

        with pytest.raises(EmptyResponseException):
            await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_rate_limit_exception_retries(target: OpenAIChatTarget):
    message = Message(message_pieces=[MessagePiece(role="user", conversation_id="12345", original_value="Hello")])

    # Create proper mock request and response for RateLimitError
    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    mock_response = httpx.Response(429, text="Rate Limit Reached", request=mock_request)
    side_effect = RateLimitError("Rate Limit Reached", response=mock_response, body="Rate limit reached")

    # Mock the OpenAI SDK client method
    target._async_client.chat.completions.create = AsyncMock(side_effect=side_effect)  # type: ignore[method-assign]

    with pytest.raises(RateLimitException):
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_bad_request_error(target: OpenAIChatTarget):
    # Create proper mock request and response for BadRequestError (without content_filter)
    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    mock_response = httpx.Response(400, text="Bad Request Error", request=mock_request)
    side_effect = BadRequestError("Bad Request Error", response=mock_response, body="Bad request")

    message = Message(message_pieces=[MessagePiece(role="user", conversation_id="1236748", original_value="Hello")])

    # Mock the OpenAI SDK client method
    target._async_client.chat.completions.create = AsyncMock(side_effect=side_effect)  # type: ignore[method-assign]

    # Non-content-filter BadRequestError should be re-raised
    with pytest.raises(Exception):  # Will raise since handle_bad_request_exception re-raises non-content-filter errors
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_content_filter_200(target: OpenAIChatTarget):
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="567567",
                original_value="A prompt for something harmful that gets filtered.",
            )
        ]
    )

    # Mock the OpenAI SDK client to return content_filter finish_reason
    mock_completion = create_mock_completion(
        content="Offending content omitted since this is just a test.", finish_reason="content_filter"
    )
    target._async_client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=mock_completion
    )

    response = await target.send_prompt_async(message=message)
    assert len(response) == 1
    assert len(response[0].message_pieces) == 1
    assert response[0].message_pieces[0].response_error == "blocked"
    assert response[0].message_pieces[0].converted_value_data_type == "error"


def test_validate_request_unsupported_data_types(target: OpenAIChatTarget):
    image_piece = get_image_message_piece()
    image_piece.converted_value_data_type = "new_unknown_type"  # type: ignore
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Hello",
                converted_value_data_type="text",
                conversation_id=image_piece.conversation_id,
            ),
            image_piece,
        ]
    )

    with pytest.raises(ValueError) as excinfo:
        target._validate_request(message=message)

    assert "This target only supports text, image_path, and audio_path." in str(excinfo.value), (
        "Error not raised for unsupported data types"
    )

    os.remove(image_piece.original_value)


def test_is_json_response_supported(target: OpenAIChatTarget):
    assert target.is_json_response_supported() is True


def test_inheritance_from_prompt_chat_target(target: OpenAIChatTarget):
    """Test that OpenAIChatTarget properly inherits from PromptChatTarget."""
    assert isinstance(target, PromptChatTarget), "OpenAIChatTarget must inherit from PromptChatTarget"


def test_inheritance_from_prompt_chat_target_base():
    """Test that OpenAIChatTargetBase properly inherits from PromptChatTarget."""

    # Create a minimal instance to test inheritance
    target = OpenAIChatTarget(model_name="test-model", endpoint="https://test.com", api_key="test-key")
    assert isinstance(target, PromptChatTarget), (
        "OpenAIChatTarget must inherit from PromptChatTarget through OpenAIChatTargetBase"
    )


def test_is_response_format_json_supported(target: OpenAIChatTarget):
    message_piece = MessagePiece(
        role="user",
        original_value="original prompt text",
        converted_value="Hello, how are you?",
        conversation_id="conversation_1",
        sequence=0,
        prompt_metadata={"response_format": "json"},
    )

    result = target.is_response_format_json(message_piece)
    assert isinstance(result, bool)
    assert result is True


def test_is_response_format_json_schema_supported(target: OpenAIChatTarget):
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    message_piece = MessagePiece(
        role="user",
        original_value="original prompt text",
        converted_value="Hello, how are you?",
        conversation_id="conversation_1",
        sequence=0,
        prompt_metadata={
            "response_format": "json",
            "json_schema": json.dumps(schema),
        },
    )

    result = target.is_response_format_json(message_piece)
    assert result


def test_is_response_format_json_no_metadata(target: OpenAIChatTarget):
    message_piece = MessagePiece(
        role="user",
        original_value="original prompt text",
        converted_value="Hello, how are you?",
        conversation_id="conversation_1",
        sequence=0,
        prompt_metadata=None,
    )

    result = target.is_response_format_json(message_piece)

    assert result is False


@pytest.mark.asyncio
async def test_send_prompt_async_content_filter_400(target: OpenAIChatTarget):
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_conversation.return_value = []
    mock_memory.add_message_to_memory = AsyncMock()
    target._memory = mock_memory

    with (
        patch.object(target, "_validate_request"),
        patch.object(target, "_construct_request_body", new_callable=AsyncMock) as mock_construct,
    ):
        mock_construct.return_value = {"model": "gpt-4", "messages": [], "stream": False}

        # Create proper mock request and response for BadRequestError with content_filter
        mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        error_json = {"error": {"code": "content_filter"}}
        mock_response = httpx.Response(400, text=json.dumps(error_json), request=mock_request)
        status_error = BadRequestError("Bad Request", response=mock_response, body=error_json)

        message_piece = MessagePiece(
            role="user",
            conversation_id="cid",
            original_value="hello",
            converted_value="hello",
            original_value_data_type="text",
            converted_value_data_type="text",
        )
        message = Message(message_pieces=[message_piece])

        # Mock the OpenAI SDK client method
        target._async_client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
            side_effect=status_error
        )

        result = await target.send_prompt_async(message=message)
        assert len(result) == 1
        assert result[0].message_pieces[0].converted_value_data_type == "error"
        assert result[0].message_pieces[0].response_error == "blocked"


@pytest.mark.asyncio
async def test_send_prompt_async_other_http_error(patch_central_database):
    target = OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )
    message_piece = MessagePiece(
        role="user",
        conversation_id="cid",
        original_value="hello",
        converted_value="hello",
        original_value_data_type="text",
        converted_value_data_type="text",
    )
    message = Message(message_pieces=[message_piece])
    target._memory = MagicMock()
    target._memory.get_conversation.return_value = []

    # Create proper mock request and response for APIStatusError
    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    mock_response = httpx.Response(500, text="Internal Server Error", request=mock_request)
    status_error = APIStatusError("Internal Server Error", response=mock_response, body=None)

    # Mock the OpenAI SDK client method
    target._async_client.chat.completions.create = AsyncMock(side_effect=status_error)  # type: ignore[method-assign]

    with pytest.raises(APIStatusError):
        await target.send_prompt_async(message=message)


def test_set_auth_with_entra_auth(patch_central_database):
    """Test that Entra authentication is properly configured."""

    def mock_token_provider():
        return "mock-entra-token"

    target = OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="https://test.openai.azure.com",
        api_key=mock_token_provider,
    )

    # Verify token provider was stored as api_key
    assert callable(target._api_key)
    assert target._api_key() == "mock-entra-token"


def test_set_auth_with_api_key(patch_central_database):
    """Test that API key authentication is properly configured."""
    target = OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="https://test.openai.azure.com",
        api_key="test_api_key_456",
    )

    # Verify API key was stored correctly
    assert target._api_key == "test_api_key_456"


def test_url_validation_no_warning_for_custom_endpoint(caplog, patch_central_database):
    """Test that URL validation doesn't warn for custom endpoint paths."""
    with patch.dict(os.environ, {}, clear=True):
        with caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint="https://some.provider.com/v1/custom/path",  # Incorrect endpoint
                api_key="test-key",
            )

    # Should NOT warn about custom paths - they could be for custom endpoints
    warning_logs = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warning_logs) == 0
    assert target


def test_url_validation_no_warning_for_correct_azure_endpoint(caplog, patch_central_database):
    """Test that URL validation doesn't warn for correct Azure endpoints."""
    with patch.dict(os.environ, {}, clear=True):
        with caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint="https://myservice.openai.azure.com/openai/deployments/gpt-4/chat/completions",
                api_key="test-key",
            )

    # Should not have URL validation warnings
    warning_logs = [record for record in caplog.records if record.levelno >= logging.WARNING]
    endpoint_warnings = [log for log in warning_logs if "The provided endpoint URL" in log.message]
    assert len(endpoint_warnings) == 0
    assert target


def test_azure_endpoint_with_api_version_query_param(patch_central_database):
    """Test that Azure endpoints with api-version query parameter are handled correctly."""
    with patch.dict(os.environ, {}, clear=True):
        target = OpenAIChatTarget(
            model_name="gpt-4",
            endpoint="https://test.openai.azure.com/openai/deployments/gpt-4/chat/completions?api-version=2024-02-15",
            api_key="test-key",
        )

    # Verify the SDK client was initialized with the base endpoint and api_version extracted
    assert target._async_client is not None
    # The AsyncAzureOpenAI client should have been initialized with the base URL (no query params, no path)
    # and the api_version as a separate parameter


def test_azure_endpoint_new_format_openai_v1(patch_central_database):
    """Test that Azure endpoints with /openai/v1 format are handled correctly."""
    with patch.dict(os.environ, {}, clear=True):
        target = OpenAIChatTarget(
            model_name="gpt-4",
            endpoint="https://test.openai.azure.com/openai/v1?api-version=2025-03-01-preview",
            api_key="test-key",
        )

    # Verify the SDK client was initialized
    assert target._async_client is not None
    # The AsyncAzureOpenAI client should have been initialized with just the base URL


def test_azure_responses_endpoint_format(patch_central_database):
    """Test that Azure responses endpoint format is handled correctly."""
    with patch.dict(os.environ, {}, clear=True):
        target = OpenAIResponseTarget(
            model_name="o4-mini",
            endpoint="https://test.openai.azure.com/openai/responses?api-version=2025-03-01-preview",
            api_key="test-key",
        )

    # Verify the SDK client was initialized
    assert target._async_client is not None


def test_azure_responses_endpoint_new_format(patch_central_database):
    """Test that Azure responses endpoint with /openai/v1 format is handled correctly."""
    with patch.dict(os.environ, {}, clear=True):
        target = OpenAIResponseTarget(
            model_name="o4-mini",
            endpoint="https://test.openai.azure.com/openai/v1?api-version=2025-03-01-preview",
            api_key="test-key",
        )

    # Verify the SDK client was initialized
    assert target._async_client is not None


def test_invalid_temperature_raises(patch_central_database):
    """Test that invalid temperature values raise PyritException."""
    with pytest.raises(PyritException, match="temperature must be between 0 and 2"):
        OpenAIChatTarget(
            model_name="gpt-4",
            endpoint="https://test.com",
            api_key="test",
            temperature=-0.1,
        )

    with pytest.raises(PyritException, match="temperature must be between 0 and 2"):
        OpenAIChatTarget(
            model_name="gpt-4",
            endpoint="https://test.com",
            api_key="test",
            temperature=2.1,
        )


def test_invalid_top_p_raises(patch_central_database):
    """Test that invalid top_p values raise PyritException."""
    with pytest.raises(PyritException, match="top_p must be between 0 and 1"):
        OpenAIChatTarget(
            model_name="gpt-4",
            endpoint="https://test.com",
            api_key="test",
            top_p=-0.1,
        )

    with pytest.raises(PyritException, match="top_p must be between 0 and 1"):
        OpenAIChatTarget(
            model_name="gpt-4",
            endpoint="https://test.com",
            api_key="test",
            top_p=1.1,
        )


@pytest.mark.asyncio
async def test_content_filter_finish_reason_error(
    target: OpenAIChatTarget, sample_conversations: MutableSequence[MessagePiece]
):
    """Test ContentFilterFinishReasonError from SDK is handled correctly."""
    message_piece = sample_conversations[0]
    message_piece.conversation_id = "test-conv-id"
    request = Message(message_pieces=[message_piece])

    # ContentFilterFinishReasonError takes no arguments
    content_filter_error = ContentFilterFinishReasonError()

    with patch.object(target._async_client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = content_filter_error

        response = await target.send_prompt_async(message=request)

        # Should return a blocked response (wrapped in list)
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].response_error == "blocked"


@pytest.mark.asyncio
async def test_bad_request_with_dict_body_content_filter(
    target: OpenAIChatTarget, sample_conversations: MutableSequence[MessagePiece]
):
    """Test BadRequestError with dict body containing content_filter code."""
    message_piece = sample_conversations[0]
    message_piece.conversation_id = "test-conv-id"
    request = Message(message_pieces=[message_piece])

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = '{"error": {"code": "content_filter", "message": "Filtered"}}'
    mock_response.headers = {"x-request-id": "test-123"}

    bad_request_error = BadRequestError(
        "Bad request", response=mock_response, body={"error": {"code": "content_filter"}}
    )

    with patch.object(target._async_client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = bad_request_error

        response = await target.send_prompt_async(message=request)

        # Should detect content filter from dict body
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].response_error == "blocked"


@pytest.mark.asyncio
async def test_bad_request_with_string_content_filter(
    target: OpenAIChatTarget, sample_conversations: MutableSequence[MessagePiece]
):
    """Test BadRequestError with non-parseable string containing 'content_filter'."""
    message_piece = sample_conversations[0]
    message_piece.conversation_id = "test-conv-id"
    request = Message(message_pieces=[message_piece])

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Error: content_filter violation detected"
    mock_response.headers = {"x-request-id": "test-123"}

    bad_request_error = BadRequestError(
        "Bad request", response=mock_response, body="Error: content_filter violation detected"
    )

    with patch.object(target._async_client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = bad_request_error

        response = await target.send_prompt_async(message=request)

        # Should detect content filter from string matching
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].response_error == "blocked"


@pytest.mark.asyncio
async def test_api_status_error_429(target: OpenAIChatTarget, sample_conversations: MutableSequence[MessagePiece]):
    """Test APIStatusError with status 429 raises RateLimitException."""
    message_piece = sample_conversations[0]
    message_piece.conversation_id = "test-conv-id"
    request = Message(message_pieces=[message_piece])

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Too many requests"
    mock_response.headers = {"x-request-id": "test-123"}

    api_error = APIStatusError("Too many requests", response=mock_response, body={})
    api_error.status_code = 429

    with patch.object(target._async_client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = api_error

        with pytest.raises(RateLimitException):
            await target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_api_status_error_non_429(target: OpenAIChatTarget, sample_conversations: MutableSequence[MessagePiece]):
    """Test APIStatusError with non-429 status is re-raised."""
    message_piece = sample_conversations[0]
    message_piece.conversation_id = "test-conv-id"
    request = Message(message_pieces=[message_piece])

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal server error"
    mock_response.headers = {"x-request-id": "test-123"}

    api_error = APIStatusError("Internal server error", response=mock_response, body={})
    api_error.status_code = 500

    with patch.object(target._async_client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = api_error

        with pytest.raises(APIStatusError):
            await target.send_prompt_async(message=request)


# Unit tests for override methods


def test_check_content_filter_detects_filtered_response(target: OpenAIChatTarget):
    """Test _check_content_filter detects content_filter finish_reason."""
    mock_response = create_mock_completion(content="", finish_reason="content_filter")
    assert target._check_content_filter(mock_response) is True


def test_check_content_filter_no_filter(target: OpenAIChatTarget):
    """Test _check_content_filter returns False for normal responses."""
    mock_response = create_mock_completion(content="Hello", finish_reason="stop")
    assert target._check_content_filter(mock_response) is False


def test_check_content_filter_length_finish(target: OpenAIChatTarget):
    """Test _check_content_filter returns False for length finish_reason."""
    mock_response = create_mock_completion(content="Hello", finish_reason="length")
    assert target._check_content_filter(mock_response) is False


def test_validate_response_success_stop(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response passes for valid stop response."""
    mock_response = create_mock_completion(content="Hello", finish_reason="stop")
    result = target._validate_response(mock_response, dummy_text_message_piece)
    assert result is None


def test_validate_response_success_length(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response passes for valid length response."""
    mock_response = create_mock_completion(content="Hello", finish_reason="length")
    result = target._validate_response(mock_response, dummy_text_message_piece)
    assert result is None


def test_validate_response_no_choices(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response raises for missing choices."""
    mock_response = create_mock_completion(content="Hello", finish_reason="stop")
    mock_response.choices = []

    with pytest.raises(PyritException, match="No choices returned"):
        target._validate_response(mock_response, dummy_text_message_piece)


def test_validate_response_unknown_finish_reason(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response raises for unknown finish_reason."""
    mock_response = create_mock_completion(content="Hello", finish_reason="unexpected")

    with pytest.raises(PyritException, match="Unknown finish_reason"):
        target._validate_response(mock_response, dummy_text_message_piece)


def test_validate_response_empty_content(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response raises for empty content."""
    mock_response = create_mock_completion(content="", finish_reason="stop")

    with pytest.raises(EmptyResponseException, match="empty response"):
        target._validate_response(mock_response, dummy_text_message_piece)


def test_validate_response_none_content(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response raises for None content."""
    mock_response = create_mock_completion(content=None, finish_reason="stop")
    mock_response.choices[0].message.content = None

    with pytest.raises(EmptyResponseException, match="empty response"):
        target._validate_response(mock_response, dummy_text_message_piece)


@pytest.mark.asyncio
async def test_construct_message_from_response(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    """Test _construct_message_from_response extracts content correctly."""
    mock_response = create_mock_completion(content="Hello from AI", finish_reason="stop")

    result = await target._construct_message_from_response(mock_response, dummy_text_message_piece)

    assert isinstance(result, Message)
    assert len(result.message_pieces) == 1
    assert result.message_pieces[0].converted_value == "Hello from AI"


# Tests for underlying_model parameter and get_identifier


def test_get_identifier_uses_model_name_when_no_underlying_model(patch_central_database):
    """Test that get_identifier uses model_name when underlying_model is not provided."""
    target = OpenAIChatTarget(
        model_name="my-deployment",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    identifier = target.get_identifier()

    assert identifier["model_name"] == "my-deployment"
    assert identifier["__type__"] == "OpenAIChatTarget"


def test_get_identifier_uses_underlying_model_when_provided_as_param(patch_central_database):
    """Test that get_identifier uses underlying_model when passed as a parameter."""
    target = OpenAIChatTarget(
        model_name="my-deployment",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        underlying_model="gpt-4o",
    )

    identifier = target.get_identifier()

    assert identifier["model_name"] == "gpt-4o"
    assert identifier["__type__"] == "OpenAIChatTarget"


def test_get_identifier_uses_underlying_model_from_env_var(patch_central_database):
    """Test that get_identifier uses underlying_model from environment variable."""
    with patch.dict(os.environ, {"OPENAI_CHAT_UNDERLYING_MODEL": "gpt-4o"}):
        target = OpenAIChatTarget(
            model_name="my-deployment",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )

        identifier = target.get_identifier()

        assert identifier["model_name"] == "gpt-4o"


def test_underlying_model_param_takes_precedence_over_env_var(patch_central_database):
    """Test that underlying_model parameter takes precedence over environment variable."""
    with patch.dict(os.environ, {"OPENAI_CHAT_UNDERLYING_MODEL": "gpt-4o-from-env"}):
        target = OpenAIChatTarget(
            model_name="my-deployment",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
            underlying_model="gpt-4o-from-param",
        )

        identifier = target.get_identifier()

        assert identifier["model_name"] == "gpt-4o-from-param"


def test_get_identifier_includes_endpoint(patch_central_database):
    """Test that get_identifier includes the endpoint."""
    target = OpenAIChatTarget(
        model_name="my-deployment",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    identifier = target.get_identifier()

    assert identifier["endpoint"] == "https://mock.azure.com/"


def test_get_identifier_includes_temperature_when_set(patch_central_database):
    """Test that get_identifier includes temperature when configured."""
    target = OpenAIChatTarget(
        model_name="my-deployment",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        temperature=0.7,
    )

    identifier = target.get_identifier()

    assert identifier["temperature"] == 0.7


def test_get_identifier_includes_top_p_when_set(patch_central_database):
    """Test that get_identifier includes top_p when configured."""
    target = OpenAIChatTarget(
        model_name="my-deployment",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        top_p=0.9,
    )

    identifier = target.get_identifier()

    assert identifier["top_p"] == 0.9


# ============================================================================
# Audio Response Config Tests
# ============================================================================


def test_init_with_audio_response_config(patch_central_database):
    """Test initialization with audio_response_config."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    assert target._audio_response_config is not None
    assert target._audio_response_config.voice == "alloy"
    assert target._audio_response_config.audio_format == "wav"


def test_init_audio_config_extra_body_params_merged(patch_central_database):
    """Test that audio config parameters are merged with extra_body_parameters."""
    audio_config = OpenAIChatAudioConfig(voice="coral", audio_format="mp3")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
        extra_body_parameters={"custom_param": "value"},
    )

    # The audio config should add modalities and audio to extra body
    assert target._extra_body_parameters.get("modalities") == ["text", "audio"]
    assert target._extra_body_parameters.get("audio") == {"voice": "coral", "format": "mp3"}
    assert target._extra_body_parameters.get("custom_param") == "value"


@pytest.mark.asyncio
async def test_construct_request_body_with_audio_config(patch_central_database, dummy_text_message_piece: MessagePiece):
    """Test that request body includes audio modalities when audio config is set."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)

    body = await target._construct_request_body(conversation=[request], json_config=jrc)

    assert body.get("modalities") == ["text", "audio"]
    assert body.get("audio") == {"voice": "alloy", "format": "wav"}


# ============================================================================
# Audio History Stripping Tests
# ============================================================================


def _create_audio_message_piece(role: str, data_type: str = "audio_path") -> MessagePiece:
    """Helper to create a MessagePiece for audio skip tests."""
    return MessagePiece(
        role=role,
        original_value="/path/to/audio.wav",
        converted_value="/path/to/audio.wav",
        original_value_data_type=data_type,
        converted_value_data_type=data_type,
        conversation_id="test-conv",
    )


def test_should_skip_sending_audio_assistant_role(patch_central_database):
    """Test that audio is always skipped for assistant messages."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    # Assistant audio should always be skipped regardless of other conditions
    message_piece = _create_audio_message_piece(role="assistant")
    result = target._should_skip_sending_audio(
        message_piece=message_piece,
        is_last_message=True,
        has_text_piece=True,
    )
    assert result is True

    # Even when it's the last message with no text piece
    result = target._should_skip_sending_audio(
        message_piece=message_piece,
        is_last_message=True,
        has_text_piece=False,
    )
    assert result is True


def test_should_skip_sending_audio_user_history_with_transcript(patch_central_database):
    """Test that historical user audio is skipped when transcript exists and prefer_transcript_for_history is True."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav", prefer_transcript_for_history=True)
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    # Historical user audio with transcript should be skipped
    message_piece = _create_audio_message_piece(role="user")
    result = target._should_skip_sending_audio(
        message_piece=message_piece,
        is_last_message=False,  # Historical message
        has_text_piece=True,  # Has transcript
    )
    assert result is True


def test_should_skip_sending_audio_user_history_without_transcript(patch_central_database):
    """Test that historical user audio is NOT skipped when no transcript exists."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav", prefer_transcript_for_history=True)
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    # Historical user audio without transcript should NOT be skipped
    message_piece = _create_audio_message_piece(role="user")
    result = target._should_skip_sending_audio(
        message_piece=message_piece,
        is_last_message=False,  # Historical message
        has_text_piece=False,  # No transcript
    )
    assert result is False


def test_should_skip_sending_audio_current_user_message(patch_central_database):
    """Test that the current (last) user audio is NOT skipped."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav", prefer_transcript_for_history=True)
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    # Current user audio should NOT be skipped even with transcript
    message_piece = _create_audio_message_piece(role="user")
    result = target._should_skip_sending_audio(
        message_piece=message_piece,
        is_last_message=True,  # Current message
        has_text_piece=True,
    )
    assert result is False


def test_should_skip_sending_audio_prefer_transcript_disabled(patch_central_database):
    """Test that audio is NOT skipped when prefer_transcript_for_history is False."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav", prefer_transcript_for_history=False)
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    # Historical user audio should NOT be skipped when prefer_transcript_for_history is False
    message_piece = _create_audio_message_piece(role="user")
    result = target._should_skip_sending_audio(
        message_piece=message_piece,
        is_last_message=False,
        has_text_piece=True,
    )
    assert result is False


def test_should_skip_sending_audio_no_audio_config(patch_central_database):
    """Test that audio is NOT skipped when no audio config is set."""
    target = OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )

    # Without audio config, historical audio should NOT be skipped (for user)
    message_piece = _create_audio_message_piece(role="user")
    result = target._should_skip_sending_audio(
        message_piece=message_piece,
        is_last_message=False,
        has_text_piece=True,
    )
    assert result is False


def test_should_skip_sending_audio_non_audio_type(patch_central_database):
    """Test that non-audio data types are never skipped by this method."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    # Text type should not be skipped
    text_piece = _create_audio_message_piece(role="user", data_type="text")
    result = target._should_skip_sending_audio(
        message_piece=text_piece,
        is_last_message=False,
        has_text_piece=True,
    )
    assert result is False

    # Image type should not be skipped
    image_piece = _create_audio_message_piece(role="assistant", data_type="image_path")
    result = target._should_skip_sending_audio(
        message_piece=image_piece,
        is_last_message=True,
        has_text_piece=False,
    )
    assert result is False


@pytest.mark.asyncio
async def test_build_chat_messages_strips_audio_from_history(patch_central_database):
    """Test that audio is stripped from historical messages when building chat messages."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav", prefer_transcript_for_history=True)
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    conv_id = "test-conv-id"

    # Create a historical message with both text (transcript) and audio
    historical_message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Hello from audio",
                converted_value="Hello from audio",
                original_value_data_type="text",
                converted_value_data_type="text",
                conversation_id=conv_id,
            ),
            MessagePiece(
                role="user",
                original_value="/path/to/audio.wav",
                converted_value="/path/to/audio.wav",
                original_value_data_type="audio_path",
                converted_value_data_type="audio_path",
                conversation_id=conv_id,
            ),
        ]
    )

    # Create the current (last) message with text
    current_message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Follow up question",
                converted_value="Follow up question",
                original_value_data_type="text",
                converted_value_data_type="text",
                conversation_id=conv_id,
            ),
        ]
    )

    # Build chat messages
    messages = await target._build_chat_messages_for_multi_modal_async([historical_message, current_message])

    # Verify the historical message only has text (audio was stripped)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    # The historical message should only have the text content, not the audio
    historical_content = messages[0]["content"]
    assert len(historical_content) == 1
    assert historical_content[0]["type"] == "text"
    assert historical_content[0]["text"] == "Hello from audio"

    # Current message should just have text
    assert messages[1]["content"][0]["type"] == "text"
    assert messages[1]["content"][0]["text"] == "Follow up question"


# ============================================================================
# Audio Response Saving Tests
# ============================================================================


@pytest.mark.asyncio
async def test_save_audio_response_async_wav_format(patch_central_database):
    """Test saving audio response with wav format."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    # Create some dummy audio data
    audio_bytes = b"fake wav audio data"
    audio_data_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    with patch("pyrit.prompt_target.openai.openai_chat_target.data_serializer_factory") as mock_factory:
        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/saved/audio.wav"
        mock_serializer.save_data = AsyncMock()
        mock_factory.return_value = mock_serializer

        result = await target._save_audio_response_async(audio_data_base64=audio_data_base64)

        # Verify factory was called with correct extension
        mock_factory.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="audio_path",
            extension=".wav",
        )

        # Verify save_data was called (not save_formatted_audio for wav)
        mock_serializer.save_data.assert_called_once_with(audio_bytes)
        mock_serializer.save_formatted_audio.assert_not_called()

        assert result == "/path/to/saved/audio.wav"


@pytest.mark.asyncio
async def test_save_audio_response_async_mp3_format(patch_central_database):
    """Test saving audio response with mp3 format."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="mp3")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    audio_bytes = b"fake mp3 audio data"
    audio_data_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    with patch("pyrit.prompt_target.openai.openai_chat_target.data_serializer_factory") as mock_factory:
        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/saved/audio.mp3"
        mock_serializer.save_data = AsyncMock()
        mock_factory.return_value = mock_serializer

        result = await target._save_audio_response_async(audio_data_base64=audio_data_base64)

        # Verify factory was called with correct extension
        mock_factory.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="audio_path",
            extension=".mp3",
        )

        # Verify save_data was called (not save_formatted_audio for mp3)
        mock_serializer.save_data.assert_called_once_with(audio_bytes)

        assert result == "/path/to/saved/audio.mp3"


@pytest.mark.asyncio
async def test_save_audio_response_async_pcm16_format(patch_central_database):
    """Test saving audio response with pcm16 format adds WAV headers."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="pcm16")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    # Create some dummy raw PCM audio data
    audio_bytes = b"\x00\x01\x02\x03" * 100  # Raw PCM16 data
    audio_data_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    with patch("pyrit.prompt_target.openai.openai_chat_target.data_serializer_factory") as mock_factory:
        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/saved/audio.wav"
        mock_serializer.save_formatted_audio = AsyncMock()
        mock_factory.return_value = mock_serializer

        result = await target._save_audio_response_async(audio_data_base64=audio_data_base64)

        # Verify factory was called with .wav extension (pcm16 gets saved as wav)
        mock_factory.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="audio_path",
            extension=".wav",
        )

        # Verify save_formatted_audio was called with correct PCM16 parameters
        mock_serializer.save_formatted_audio.assert_called_once_with(
            data=audio_bytes,
            num_channels=1,
            sample_width=2,
            sample_rate=24000,
        )
        # save_data should not be called for pcm16
        mock_serializer.save_data.assert_not_called()

        assert result == "/path/to/saved/audio.wav"


@pytest.mark.asyncio
async def test_save_audio_response_async_flac_format(patch_central_database):
    """Test saving audio response with flac format."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="flac")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    audio_bytes = b"fake flac audio data"
    audio_data_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    with patch("pyrit.prompt_target.openai.openai_chat_target.data_serializer_factory") as mock_factory:
        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/saved/audio.flac"
        mock_serializer.save_data = AsyncMock()
        mock_factory.return_value = mock_serializer

        result = await target._save_audio_response_async(audio_data_base64=audio_data_base64)

        mock_factory.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="audio_path",
            extension=".flac",
        )
        mock_serializer.save_data.assert_called_once_with(audio_bytes)

        assert result == "/path/to/saved/audio.flac"


@pytest.mark.asyncio
async def test_save_audio_response_async_opus_format(patch_central_database):
    """Test saving audio response with opus format."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="opus")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    audio_bytes = b"fake opus audio data"
    audio_data_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    with patch("pyrit.prompt_target.openai.openai_chat_target.data_serializer_factory") as mock_factory:
        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/saved/audio.opus"
        mock_serializer.save_data = AsyncMock()
        mock_factory.return_value = mock_serializer

        result = await target._save_audio_response_async(audio_data_base64=audio_data_base64)

        mock_factory.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="audio_path",
            extension=".opus",
        )
        mock_serializer.save_data.assert_called_once_with(audio_bytes)

        assert result == "/path/to/saved/audio.opus"


@pytest.mark.asyncio
async def test_save_audio_response_async_no_config_defaults_to_wav(patch_central_database):
    """Test saving audio response defaults to wav when no audio config is set."""
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        # No audio_response_config
    )

    audio_bytes = b"fake audio data"
    audio_data_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    with patch("pyrit.prompt_target.openai.openai_chat_target.data_serializer_factory") as mock_factory:
        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/saved/audio.wav"
        mock_serializer.save_data = AsyncMock()
        mock_factory.return_value = mock_serializer

        result = await target._save_audio_response_async(audio_data_base64=audio_data_base64)

        # Should default to .wav extension
        mock_factory.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="audio_path",
            extension=".wav",
        )
        mock_serializer.save_data.assert_called_once_with(audio_bytes)

        assert result == "/path/to/saved/audio.wav"


@pytest.mark.asyncio
async def test_construct_message_from_response_audio_transcript_has_metadata(
    patch_central_database, dummy_text_message_piece: MessagePiece
):
    """Test that audio transcript text pieces include metadata to distinguish from regular text."""
    audio_config = OpenAIChatAudioConfig(voice="alloy", audio_format="wav")
    target = OpenAIChatTarget(
        model_name="gpt-4o-audio-preview",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        audio_response_config=audio_config,
    )

    # Create mock response with audio (transcript + data)
    mock_response = MagicMock(spec=ChatCompletion)
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].finish_reason = "stop"
    mock_response.choices[0].message.content = None  # No text content
    mock_response.choices[0].message.audio = MagicMock()
    mock_response.choices[0].message.audio.transcript = "This is the audio transcript"
    mock_response.choices[0].message.audio.data = base64.b64encode(b"fake audio").decode("utf-8")
    mock_response.choices[0].message.tool_calls = None

    with patch("pyrit.prompt_target.openai.openai_chat_target.data_serializer_factory") as mock_factory:
        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/audio.wav"
        mock_serializer.save_data = AsyncMock()
        mock_factory.return_value = mock_serializer

        result = await target._construct_message_from_response(mock_response, dummy_text_message_piece)

        # Should have 2 pieces: transcript (text) and audio file (audio_path)
        assert len(result.message_pieces) == 2

        transcript_piece = result.message_pieces[0]
        audio_piece = result.message_pieces[1]

        # Verify transcript piece
        assert transcript_piece.converted_value_data_type == "text"
        assert transcript_piece.converted_value == "This is the audio transcript"
        assert transcript_piece.prompt_metadata is not None
        assert transcript_piece.prompt_metadata.get("transcription") == "audio"

        # Verify audio piece
        assert audio_piece.converted_value_data_type == "audio_path"
        assert audio_piece.converted_value == "/path/to/audio.wav"


@pytest.mark.asyncio
async def test_construct_message_from_response_text_content_no_transcript_metadata(
    target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece
):
    """Test that regular text content does not have transcript metadata."""
    mock_response = create_mock_completion(content="Regular text response", finish_reason="stop")

    result = await target._construct_message_from_response(mock_response, dummy_text_message_piece)

    assert len(result.message_pieces) == 1
    text_piece = result.message_pieces[0]

    assert text_piece.converted_value_data_type == "text"
    assert text_piece.converted_value == "Regular text response"
    # Regular text should not have transcription metadata
    assert text_piece.prompt_metadata is None or text_piece.prompt_metadata.get("transcription") is None


# ============================================================================
# Tool Calling Tests
# ============================================================================


def create_mock_completion_with_tool_calls(tool_calls: list, finish_reason: str = "tool_calls"):
    """Helper to create a mock OpenAI completion response with tool calls."""
    mock_completion = MagicMock(spec=ChatCompletion)
    mock_completion.choices = [MagicMock()]
    mock_completion.choices[0].finish_reason = finish_reason
    mock_completion.choices[0].message.content = None
    mock_completion.choices[0].message.audio = None
    mock_completion.choices[0].message.tool_calls = tool_calls
    mock_completion.model_dump_json.return_value = json.dumps(
        {"choices": [{"finish_reason": finish_reason, "message": {"content": None, "tool_calls": []}}]}
    )
    return mock_completion


def create_mock_tool_call(call_id: str, function_name: str, arguments: str):
    """Helper to create a mock tool call object."""
    mock_tool_call = MagicMock()
    mock_tool_call.id = call_id
    mock_tool_call.type = "function"
    mock_tool_call.function = MagicMock()
    mock_tool_call.function.name = function_name
    mock_tool_call.function.arguments = arguments
    return mock_tool_call


def test_validate_response_tool_calls_finish_reason(target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response accepts tool_calls finish_reason."""
    tool_call = create_mock_tool_call("call_123", "get_weather", '{"location": "NYC"}')
    mock_response = create_mock_completion_with_tool_calls([tool_call], finish_reason="tool_calls")

    # Should not raise - tool_calls is a valid finish reason
    result = target._validate_response(mock_response, dummy_text_message_piece)
    assert result is None


def test_detect_response_content_with_tool_calls(target: OpenAIChatTarget):
    """Test _detect_response_content correctly identifies tool calls."""
    mock_message = MagicMock()
    mock_message.content = None
    mock_message.audio = None

    tool_call = create_mock_tool_call("call_123", "get_weather", '{"location": "NYC"}')
    mock_message.tool_calls = [tool_call]

    has_content, has_audio, has_tool_calls = target._detect_response_content(mock_message)

    assert not has_content
    assert not has_audio
    assert has_tool_calls  # Truthy - returns the list itself


def test_detect_response_content_no_tool_calls(target: OpenAIChatTarget):
    """Test _detect_response_content when tool_calls is None."""
    mock_message = MagicMock()
    mock_message.content = "Hello"
    mock_message.audio = None
    mock_message.tool_calls = None

    has_content, has_audio, has_tool_calls = target._detect_response_content(mock_message)

    assert has_content
    assert not has_audio
    assert not has_tool_calls  # Falsy - None


def test_detect_response_content_empty_tool_calls(target: OpenAIChatTarget):
    """Test _detect_response_content when tool_calls is empty list."""
    mock_message = MagicMock()
    mock_message.content = "Hello"
    mock_message.audio = None
    mock_message.tool_calls = []

    has_content, has_audio, has_tool_calls = target._detect_response_content(mock_message)

    assert has_content
    assert not has_audio
    assert not has_tool_calls  # Falsy - empty list


@pytest.mark.asyncio
async def test_construct_message_from_response_with_tool_calls(
    target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece
):
    """Test _construct_message_from_response extracts tool calls correctly."""
    tool_call = create_mock_tool_call("call_abc123", "get_current_weather", '{"location": "Seattle, WA"}')
    mock_response = create_mock_completion_with_tool_calls([tool_call])

    result = await target._construct_message_from_response(mock_response, dummy_text_message_piece)

    assert isinstance(result, Message)
    assert len(result.message_pieces) == 1

    piece = result.message_pieces[0]
    assert piece.converted_value_data_type == "function_call"

    # Verify the serialized tool call data
    tool_call_data = json.loads(piece.converted_value)
    assert tool_call_data["type"] == "function"
    assert tool_call_data["id"] == "call_abc123"
    assert tool_call_data["function"]["name"] == "get_current_weather"
    assert tool_call_data["function"]["arguments"] == '{"location": "Seattle, WA"}'


@pytest.mark.asyncio
async def test_construct_message_from_response_with_multiple_tool_calls(
    target: OpenAIChatTarget, dummy_text_message_piece: MessagePiece
):
    """Test _construct_message_from_response handles multiple tool calls."""
    tool_call1 = create_mock_tool_call("call_1", "get_weather", '{"location": "NYC"}')
    tool_call2 = create_mock_tool_call("call_2", "get_time", '{"timezone": "EST"}')
    mock_response = create_mock_completion_with_tool_calls([tool_call1, tool_call2])

    result = await target._construct_message_from_response(mock_response, dummy_text_message_piece)

    assert isinstance(result, Message)
    assert len(result.message_pieces) == 2

    # Verify both tool calls are present
    tool_call_data_1 = json.loads(result.message_pieces[0].converted_value)
    tool_call_data_2 = json.loads(result.message_pieces[1].converted_value)

    assert tool_call_data_1["function"]["name"] == "get_weather"
    assert tool_call_data_2["function"]["name"] == "get_time"


@pytest.mark.asyncio
async def test_send_prompt_with_tool_calls(target: OpenAIChatTarget):
    """Test send_prompt_async correctly handles tool call responses."""
    tool_call = create_mock_tool_call("call_test", "search_web", '{"query": "PyRIT documentation"}')
    mock_response = create_mock_completion_with_tool_calls([tool_call])

    target._async_client.chat.completions.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="test-conv",
                original_value="Search for PyRIT documentation",
                converted_value="Search for PyRIT documentation",
                original_value_data_type="text",
                converted_value_data_type="text",
            )
        ]
    )

    result = await target.send_prompt_async(message=message)

    assert len(result) == 1
    assert len(result[0].message_pieces) == 1
    assert result[0].message_pieces[0].converted_value_data_type == "function_call"

    tool_call_data = json.loads(result[0].message_pieces[0].converted_value)
    assert tool_call_data["function"]["name"] == "search_web"


def test_construct_request_body_with_tools(patch_central_database):
    """Test that tools are included in request body when specified in extra_body_parameters."""
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    target = OpenAIChatTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        extra_body_parameters={"tools": tools, "tool_choice": "auto"},
    )

    assert target._extra_body_parameters.get("tools") == tools
    assert target._extra_body_parameters.get("tool_choice") == "auto"
