# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
from collections.abc import MutableSequence
from tempfile import NamedTemporaryFile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import BadRequestError, RateLimitError
from unit.mocks import (
    get_audio_message_piece,
    get_image_message_piece,
    get_sample_conversations,
    openai_response_json_dict,
)

from pyrit.exceptions.exception_classes import (
    EmptyResponseException,
    PyritException,
    RateLimitException,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.models.json_response_config import _JsonResponseConfig
from pyrit.prompt_target import OpenAIResponseTarget, PromptChatTarget


def create_mock_response(response_dict: dict = None) -> MagicMock:
    """
    Helper function to create a mock OpenAI SDK response object.

    Args:
        response_dict: Optional dictionary to use as response data.
                      If None, uses default from openai_response_json_dict().

    Returns:
        A mock object that simulates the OpenAI SDK response with Pydantic-style attribute access.
    """
    from openai.types.responses import Response

    if response_dict is None:
        response_dict = openai_response_json_dict()

    mock_response = MagicMock(spec=Response)
    mock_response.model_dump_json.return_value = json.dumps(response_dict)
    mock_response.model_dump.return_value = response_dict  # Add model_dump for _check_content_filter

    # Set attributes based on response_dict to match OpenAI SDK Response type
    mock_response.error = response_dict.get("error")  # Should be None for successful responses
    mock_response.status = response_dict.get("status")  # Should be "completed" for successful responses

    # Mock the output sections with Pydantic-style attribute access
    if "output" in response_dict:
        output_mocks = []
        for section in response_dict["output"]:
            section_mock = MagicMock()
            # Set attributes directly for Pydantic-style access
            section_mock.type = section.get("type")

            # Handle different section types
            if section.get("type") == "message":
                # Mock content array with text attribute
                content_mocks = []
                for content_item in section.get("content", []):
                    content_mock = MagicMock()
                    content_mock.text = content_item.get("text", "")
                    content_mocks.append(content_mock)
                section_mock.content = content_mocks

            # Add model_dump for JSON serialization
            section_mock.model_dump.return_value = section
            output_mocks.append(section_mock)
        mock_response.output = output_mocks
    else:
        mock_response.output = None

    return mock_response


def fake_construct_response_from_request(request, response_text_pieces):
    return {"dummy": True, "request": request, "response": response_text_pieces}


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
def target(patch_central_database) -> OpenAIResponseTarget:
    return OpenAIResponseTarget(
        model_name="gpt-o",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )


@pytest.fixture
def openai_response_json() -> dict:
    return openai_response_json_dict()


def test_init_with_no_deployment_var_raises():
    with patch.dict(os.environ, {}, clear=True), pytest.raises(ValueError):
        OpenAIResponseTarget()


def test_init_with_no_endpoint_uri_var_raises():
    with patch.dict(os.environ, {}, clear=True), pytest.raises(ValueError):
        OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="",
            api_key="xxxxx",
        )


def test_init_with_no_additional_request_headers_var_raises():
    with patch.dict(os.environ, {}, clear=True), pytest.raises(ValueError):
        OpenAIResponseTarget(model_name="gpt-4", endpoint="", api_key="xxxxx", headers="")


@pytest.mark.asyncio()
async def test_build_input_for_multi_modal(target: OpenAIResponseTarget):
    image_request = get_image_message_piece()
    conversation_id = image_request.conversation_id
    entries = [
        Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value_data_type="text",
                    original_value="Hello 1",
                    conversation_id=conversation_id,
                ),
                image_request,
            ]
        ),
        Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value_data_type="text",
                    original_value="Hello 2",
                    conversation_id=conversation_id,
                ),
            ]
        ),
        Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value_data_type="text",
                    original_value="Hello 3",
                    conversation_id=conversation_id,
                ),
                image_request,
            ]
        ),
    ]
    with patch(
        "pyrit.common.data_url_converter.convert_local_image_to_data_url",
        return_value="data:image/jpeg;base64,encoded_string",
    ):
        messages = await target._build_input_for_multi_modal_async(entries)

    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[0]["content"][0]["type"] == "input_text"  # type: ignore[method-assign]
    assert messages[0]["content"][1]["type"] == "input_image"  # type: ignore[method-assign]
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"][0]["type"] == "output_text"  # type: ignore[method-assign]
    assert messages[2]["role"] == "user"
    assert messages[2]["content"][0]["type"] == "input_text"  # type: ignore[method-assign]
    assert messages[2]["content"][1]["type"] == "input_image"  # type: ignore[method-assign]

    os.remove(image_request.original_value)


@pytest.mark.asyncio
async def test_build_input_for_multi_modal_with_unsupported_data_types(target: OpenAIResponseTarget):
    # Like an image_path, the audio_path requires a file, but doesn't validate any contents
    entry = get_audio_message_piece()

    with pytest.raises(ValueError) as excinfo:
        await target._build_input_for_multi_modal_async([Message(message_pieces=[entry])])
    assert "Unsupported data type 'audio_path' in message index 0" in str(excinfo.value)


@pytest.mark.asyncio
async def test_construct_request_body_includes_extra_body_params(
    patch_central_database, dummy_text_message_piece: MessagePiece
):
    target = OpenAIResponseTarget(
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
async def test_construct_request_body_json_object(target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece):
    json_response_config = _JsonResponseConfig(enabled=True)
    request = Message(message_pieces=[dummy_text_message_piece])

    body = await target._construct_request_body(conversation=[request], json_config=json_response_config)
    assert body["text"] == {"format": {"type": "json_object"}}


@pytest.mark.asyncio
async def test_construct_request_body_json_schema(target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece):
    schema_object = {"type": "object", "properties": {"name": {"type": "string"}}}
    json_response_config = _JsonResponseConfig.from_metadata(
        metadata={"response_format": "json", "json_schema": schema_object}
    )
    request = Message(message_pieces=[dummy_text_message_piece])

    body = await target._construct_request_body(conversation=[request], json_config=json_response_config)
    assert body["text"] == {
        "format": {
            "type": "json_schema",
            "schema": schema_object,
            "name": "CustomSchema",
            "strict": True,
        }
    }


@pytest.mark.asyncio
async def test_construct_request_body_removes_empty_values(
    target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece
):
    request = Message(message_pieces=[dummy_text_message_piece])

    json_response_config = _JsonResponseConfig(enabled=False)
    body = await target._construct_request_body(conversation=[request], json_config=json_response_config)
    assert "max_completion_tokens" not in body
    assert "max_tokens" not in body
    assert "temperature" not in body
    assert "top_p" not in body
    assert "frequency_penalty" not in body
    assert "presence_penalty" not in body
    assert "text" not in body


@pytest.mark.asyncio
async def test_construct_request_body_serializes_text_message(
    target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece
):
    request = Message(message_pieces=[dummy_text_message_piece])

    jrc = _JsonResponseConfig.from_metadata(metadata=None)
    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert body["input"][0]["content"][0]["text"] == "dummy text"


@pytest.mark.asyncio
async def test_construct_request_body_serializes_complex_message(
    target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece
):
    image_piece = get_image_message_piece()
    dummy_text_message_piece.conversation_id = image_piece.conversation_id

    request = Message(message_pieces=[dummy_text_message_piece, image_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)

    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    messages = body["input"][0]["content"]
    assert len(messages) == 2
    assert messages[0]["type"] == "input_text"
    assert messages[1]["type"] == "input_image"


@pytest.mark.asyncio
async def test_send_prompt_async_empty_response_adds_to_memory(
    openai_response_json: dict, target: OpenAIResponseTarget
):
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
                prompt_target_identifier=ComponentIdentifier(class_name="target-identifier", class_module="test"),
                attack_identifier=ComponentIdentifier(class_name="test", class_module="test"),
                labels={"test": "test"},
            ),
            MessagePiece(
                role="user",
                conversation_id="12345679",
                original_value=tmp_file_name,
                converted_value=tmp_file_name,
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                prompt_target_identifier=ComponentIdentifier(class_name="target-identifier", class_module="test"),
                attack_identifier=ComponentIdentifier(class_name="test", class_module="test"),
                labels={"test": "test"},
            ),
        ]
    )
    # Make assistant response empty
    openai_response_json["output"][0]["content"][0]["text"] = ""
    mock_response = create_mock_response(openai_response_json)

    with patch(
        "pyrit.common.data_url_converter.convert_local_image_to_data_url",
        return_value="data:image/jpeg;base64,encoded_string",
    ):
        target._async_client.responses.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]
        target._memory = MagicMock(MemoryInterface)

        with pytest.raises(EmptyResponseException):
            await target.send_prompt_async(message=message)

        # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
        assert target._async_client.responses.create.call_count == 2


@pytest.mark.asyncio
async def test_send_prompt_async_rate_limit_exception_adds_to_memory(
    target: OpenAIResponseTarget,
):
    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = []
    mock_memory.add_message_to_memory = AsyncMock()

    target._memory = mock_memory

    message = Message(message_pieces=[MessagePiece(role="user", conversation_id="123", original_value="Hello")])

    # Mock the SDK to raise RateLimitError
    target._async_client.responses.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=RateLimitError("Rate limit exceeded", response=MagicMock(status_code=429), body=None)
    )

    with pytest.raises(RateLimitException):
        await target.send_prompt_async(message=message)
        target._memory.get_conversation.assert_called_once_with(conversation_id="123")
        target._memory.add_message_to_memory.assert_called_once_with(request=message)


@pytest.mark.asyncio
async def test_send_prompt_async_bad_request_error_adds_to_memory(target: OpenAIResponseTarget):
    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = []
    mock_memory.add_message_to_memory = AsyncMock()

    target._memory = mock_memory

    message = Message(message_pieces=[MessagePiece(role="user", conversation_id="123", original_value="Hello")])

    # Mock the SDK to raise BadRequestError (non-content-filter)
    target._async_client.responses.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=BadRequestError(
            "Bad request", response=MagicMock(status_code=400), body={"error": {"message": "Invalid request"}}
        )
    )

    with pytest.raises(BadRequestError):
        await target.send_prompt_async(message=message)
        target._memory.get_conversation.assert_called_once_with(conversation_id="123")
        target._memory.add_message_to_memory.assert_called_once_with(request=message)


@pytest.mark.asyncio
async def test_send_prompt_async(openai_response_json: dict, target: OpenAIResponseTarget):
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
                prompt_target_identifier=ComponentIdentifier(class_name="target-identifier", class_module="test"),
                attack_identifier=ComponentIdentifier(class_name="test", class_module="test"),
                labels={"test": "test"},
            ),
            MessagePiece(
                role="user",
                conversation_id="12345679",
                original_value=tmp_file_name,
                converted_value=tmp_file_name,
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                prompt_target_identifier=ComponentIdentifier(class_name="target-identifier", class_module="test"),
                attack_identifier=ComponentIdentifier(class_name="test", class_module="test"),
                labels={"test": "test"},
            ),
        ]
    )
    mock_response = create_mock_response(openai_response_json)

    with patch(
        "pyrit.common.data_url_converter.convert_local_image_to_data_url",
        return_value="data:image/jpeg;base64,encoded_string",
    ):
        target._async_client.responses.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]
        response: list[Message] = await target.send_prompt_async(message=message)
        # Response contains only assistant's response, not user's input
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].api_role == "assistant"
        assert response[0].message_pieces[0].converted_value == "hi"
    os.remove(tmp_file_name)


@pytest.mark.asyncio
async def test_send_prompt_async_empty_response_retries(openai_response_json: dict, target: OpenAIResponseTarget):
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
                prompt_target_identifier=ComponentIdentifier(class_name="target-identifier", class_module="test"),
                attack_identifier=ComponentIdentifier(class_name="test", class_module="test"),
                labels={"test": "test"},
            ),
            MessagePiece(
                role="user",
                conversation_id="12345679",
                original_value=tmp_file_name,
                converted_value=tmp_file_name,
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
                prompt_target_identifier=ComponentIdentifier(class_name="target-identifier", class_module="test"),
                attack_identifier=ComponentIdentifier(class_name="test", class_module="test"),
                labels={"test": "test"},
            ),
        ]
    )
    # Make assistant response empty
    openai_response_json["output"][0]["content"][0]["text"] = ""
    mock_response = create_mock_response(openai_response_json)

    with patch(
        "pyrit.common.data_url_converter.convert_local_image_to_data_url",
        return_value="data:image/jpeg;base64,encoded_string",
    ):
        target._async_client.responses.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]
        target._memory = MagicMock(MemoryInterface)

        with pytest.raises(EmptyResponseException):
            await target.send_prompt_async(message=message)

        # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
        assert target._async_client.responses.create.call_count == 2


@pytest.mark.asyncio
async def test_send_prompt_async_rate_limit_exception_retries(target: OpenAIResponseTarget):
    message = Message(message_pieces=[MessagePiece(role="user", conversation_id="12345", original_value="Hello")])

    # Mock SDK to raise RateLimitError
    target._async_client.responses.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=RateLimitError(
            "Rate limit exceeded", response=MagicMock(status_code=429), body="Rate limit reached"
        )
    )

    # Our code converts RateLimitError to RateLimitException, which has retry logic
    with pytest.raises(RateLimitException):
        await target.send_prompt_async(message=message)
        # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
        assert target._async_client.responses.create.call_count == 2


@pytest.mark.asyncio
async def test_send_prompt_async_bad_request_error(target: OpenAIResponseTarget):
    message = Message(message_pieces=[MessagePiece(role="user", conversation_id="1236748", original_value="Hello")])

    # Mock SDK to raise BadRequestError
    target._async_client.responses.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=BadRequestError("Bad request", response=MagicMock(status_code=400), body="Bad request")
    )

    with pytest.raises(BadRequestError):
        await target.send_prompt_async(message=message)


@pytest.mark.asyncio
async def test_send_prompt_async_content_filter(target: OpenAIResponseTarget):
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="567567",
                original_value="A prompt for something harmful that gets filtered.",
            )
        ]
    )

    # Create a response with content filter error in the status field
    content_filter_response = {
        "id": "resp_123",
        "object": "response",
        "status": None,
        "error": {
            "code": "content_filter",
            "innererror": {
                "code": "ResponsibleAIPolicyViolation",
                "content_filter_result": {"violence": {"filtered": True, "severity": "medium"}},
            },
        },
        "model": "o4-mini",
    }
    mock_response = create_mock_response(content_filter_response)
    # Fix the error object to have proper attributes
    mock_error = MagicMock()
    mock_error.code = "content_filter"
    mock_error.message = "Content filtered"
    mock_response.error = mock_error
    mock_response.model_dump_json.return_value = json.dumps(content_filter_response)
    target._async_client.responses.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

    response = await target.send_prompt_async(message=message)
    # Response contains only assistant pieces (error response), not user input
    assert len(response) == 1
    assert len(response[0].message_pieces) == 1
    assert response[0].message_pieces[0].response_error == "blocked"
    assert response[0].message_pieces[0].converted_value_data_type == "error"
    assert "content_filter_result" in response[0].message_pieces[0].converted_value


def test_validate_request_unsupported_data_types(target: OpenAIResponseTarget):
    image_piece = get_image_message_piece()
    image_piece.converted_value_data_type = "new_unknown_type"  # type: ignore[method-assign]
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

    assert "This target supports only the following data types" in str(excinfo.value), (
        "Error not raised for unsupported data types"
    )

    os.remove(image_piece.original_value)


def test_inheritance_from_prompt_chat_target(target: OpenAIResponseTarget):
    """Test that OpenAIResponseTarget properly inherits from PromptChatTarget."""
    assert isinstance(target, PromptChatTarget), "OpenAIResponseTarget must inherit from PromptChatTarget"


def test_is_response_format_json_supported(target: OpenAIResponseTarget):
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


def test_is_response_format_json_schema_supported(target: OpenAIResponseTarget):
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


def test_is_response_format_json_no_metadata(target: OpenAIResponseTarget):
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


def test_validate_request_allows_text_and_image(target: OpenAIResponseTarget):
    # Should not raise for valid types
    req = Message(
        message_pieces=[
            MessagePiece(role="user", original_value_data_type="text", original_value="Hello", conversation_id="123"),
            MessagePiece(
                role="user", original_value_data_type="image_path", original_value="fake.jpg", conversation_id="123"
            ),
        ]
    )
    target._validate_request(message=req)


def test_validate_request_raises_for_invalid_type(target: OpenAIResponseTarget):
    req = Message(
        message_pieces=[
            MessagePiece(role="user", original_value_data_type="audio_path", original_value="fake.mp3"),
        ]
    )
    with pytest.raises(ValueError) as excinfo:
        target._validate_request(message=req)
    assert "This target supports only the following data types" in str(excinfo.value)


@pytest.mark.asyncio
async def test_build_input_for_multi_modal_async_empty_conversation(target: OpenAIResponseTarget):
    # Should raise ValueError if no message pieces
    req = MagicMock()
    req.message_pieces = []
    with pytest.raises(ValueError) as excinfo:
        await target._build_input_for_multi_modal_async([req])
    assert "Failed to process conversation message at index 0: Message contains no message pieces" in str(excinfo.value)


@pytest.mark.asyncio
async def test_build_input_for_multi_modal_async_image_and_text(target: OpenAIResponseTarget):
    # Should build correct structure for text and image
    text_piece = MessagePiece(
        role="user", original_value_data_type="text", original_value="hello", conversation_id="123"
    )
    image_piece = MessagePiece(
        role="user", original_value_data_type="image_path", original_value="fake.jpg", conversation_id="123"
    )
    req = Message(message_pieces=[text_piece, image_piece])
    with patch(
        "pyrit.prompt_target.openai.openai_response_target.convert_local_image_to_data_url",
        return_value="data:image/jpeg;base64,abc",
    ):
        result = await target._build_input_for_multi_modal_async([req])
    assert result[0]["role"] == "user"
    assert result[0]["content"][0]["type"] == "input_text"
    assert result[0]["content"][1]["type"] == "input_image"
    assert result[0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_construct_request_body_filters_none(
    target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece
):
    req = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)
    body = await target._construct_request_body(conversation=[req], json_config=jrc)
    assert "max_output_tokens" not in body or body["max_output_tokens"] is None
    assert "temperature" not in body or body["temperature"] is None
    assert "top_p" not in body or body["top_p"] is None


def test_set_openai_env_configuration_vars_sets_vars():
    target = OpenAIResponseTarget(model_name="gpt", endpoint="http://test", api_key="key")
    target._set_openai_env_configuration_vars()
    assert target.model_name_environment_variable == "OPENAI_RESPONSES_MODEL"
    assert target.endpoint_environment_variable == "OPENAI_RESPONSES_ENDPOINT"
    assert target.api_key_environment_variable == "OPENAI_RESPONSES_KEY"


@pytest.mark.asyncio
async def test_build_input_for_multi_modal_async_filters_reasoning(target: OpenAIResponseTarget):
    # Prepare a conversation with a reasoning piece and a text piece
    user_prompt = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
        original_value_data_type="text",
        converted_value_data_type="text",
        conversation_id="123",
    )
    # IMPORTANT: reasoning original_value must be JSON (Responses API section)
    reasoning_section = {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "Reasoning summary."}],
    }

    response_reasoning_piece = MessagePiece(
        role="assistant",
        original_value=json.dumps(reasoning_section),
        converted_value=json.dumps(reasoning_section),
        original_value_data_type="reasoning",
        converted_value_data_type="reasoning",
        conversation_id="123",
    )
    response_text_piece = MessagePiece(
        role="assistant",
        original_value="hello there",
        converted_value="hello there",
        original_value_data_type="text",
        converted_value_data_type="text",
        conversation_id="123",
    )
    user_followup_prompt = MessagePiece(
        role="user",
        original_value="Hello indeed",
        converted_value="Hello indeed",
        original_value_data_type="text",
        converted_value_data_type="text",
        conversation_id="123",
    )
    conversation = [
        Message(message_pieces=[user_prompt]),
        Message(message_pieces=[response_reasoning_piece, response_text_piece]),
        Message(message_pieces=[user_followup_prompt]),
    ]

    # Patch image conversion (should not be called)
    with patch("pyrit.common.data_url_converter.convert_local_image_to_data_url", new_callable=AsyncMock):
        result = await target._build_input_for_multi_modal_async(conversation)

    # Reasoning is now filtered out (not sent to API), so we have 3 items:
    # 0: user role-batched message
    # 1: assistant role-batched message (text only, reasoning skipped)
    # 2: user role-batched message
    assert len(result) == 3

    # 0: user input_text
    assert result[0]["role"] == "user"
    assert result[0]["content"][0]["type"] == "input_text"

    # 1: assistant output_text (reasoning was filtered out)
    assert result[1]["role"] == "assistant"
    assert result[1]["content"][0]["type"] == "output_text"
    assert result[1]["content"][0]["text"] == "hello there"

    # 2: user input_text
    assert result[2]["role"] == "user"
    assert result[2]["content"][0]["type"] == "input_text"
    assert result[2]["content"][0]["text"] == "Hello indeed"


# New pytests
@pytest.mark.asyncio
async def test_build_input_for_multi_modal_async_system_message_maps_to_developer(target: OpenAIResponseTarget):
    system_piece = MessagePiece(
        role="system",
        original_value="You are a helpful assistant",
        converted_value="You are a helpful assistant",
        original_value_data_type="text",
        converted_value_data_type="text",
    )
    req = Message(message_pieces=[system_piece])
    items = await target._build_input_for_multi_modal_async([req])

    assert len(items) == 1
    assert items[0]["role"] == "developer"  # system -> developer mapping
    assert items[0]["content"][0]["type"] == "input_text"
    assert items[0]["content"][0]["text"] == "You are a helpful assistant"


@pytest.mark.asyncio
async def test_build_input_for_multi_modal_async_system_message_multiple_pieces(target: OpenAIResponseTarget):
    """Test that system messages can have multiple pieces and are properly handled."""
    sys1 = MessagePiece(role="system", original_value_data_type="text", original_value="A", conversation_id="123")
    sys2 = MessagePiece(role="system", original_value_data_type="text", original_value="B", conversation_id="123")
    items = await target._build_input_for_multi_modal_async([Message(message_pieces=[sys1, sys2])])
    assert len(items) == 1
    assert items[0]["role"] == "developer"
    assert len(items[0]["content"]) == 2
    assert items[0]["content"][0]["text"] == "A"
    assert items[0]["content"][1]["text"] == "B"


@pytest.mark.asyncio
async def test_build_input_for_multi_modal_async_mixed_roles_raises(target: OpenAIResponseTarget):
    """Test that Message validation prevents pieces with different roles."""
    user_piece = MessagePiece(
        role="user", original_value_data_type="text", original_value="Hello", conversation_id="123"
    )
    assistant_piece = MessagePiece(
        role="assistant", original_value_data_type="text", original_value="Hi", conversation_id="123"
    )
    # Message validation should catch this before _build_input_for_multi_modal_async
    with pytest.raises(ValueError, match="Inconsistent roles within the same message entry"):
        Message(message_pieces=[user_piece, assistant_piece])


@pytest.mark.asyncio
async def test_build_input_for_multi_modal_async_function_call_forwarded(target: OpenAIResponseTarget):
    call = {"type": "function_call", "call_id": "abc123", "name": "sum", "arguments": '{"a":2,"b":3}'}
    assistant_call_piece = MessagePiece(
        role="assistant",
        original_value=json.dumps(call),
        converted_value=json.dumps(call),
        original_value_data_type="function_call",
        converted_value_data_type="function_call",
    )
    items = await target._build_input_for_multi_modal_async([Message(message_pieces=[assistant_call_piece])])
    assert len(items) == 1
    assert items[0]["type"] == "function_call"
    assert items[0]["name"] == "sum"
    assert items[0]["call_id"] == "abc123"


@pytest.mark.asyncio
async def test_build_input_for_multi_modal_async_function_call_output_stringifies(target: OpenAIResponseTarget):
    # original_value is a function_call_output “artifact” (top level)
    output_payload = {"type": "function_call_output", "call_id": "c1", "output": {"ok": True, "value": 5}}
    piece = MessagePiece(
        role="assistant",
        original_value=json.dumps(output_payload),
        converted_value=json.dumps(output_payload),
        original_value_data_type="function_call_output",
        converted_value_data_type="function_call_output",
    )
    items = await target._build_input_for_multi_modal_async([Message(message_pieces=[piece])])
    assert len(items) == 1
    assert items[0]["type"] == "function_call_output"
    assert items[0]["call_id"] == "c1"
    # The Output must be a string for Responses API
    assert isinstance(items[0]["output"], str)
    assert json.loads(items[0]["output"]) == {"ok": True, "value": 5}


def test_make_tool_piece_serializes_output_and_sets_call_id(target: OpenAIResponseTarget):
    out = {"answer": 42}
    reference_piece = MessagePiece(
        role="user",
        original_value="test",
        conversation_id="test-conv-123",
        labels={"existing": "label"},
    )
    piece = target._make_tool_piece(out, call_id="tool-1", reference_piece=reference_piece)
    assert piece.original_value_data_type == "function_call_output"
    assert piece.conversation_id == "test-conv-123"
    assert piece.labels["call_id"] == "tool-1"
    payload = json.loads(piece.original_value)
    assert payload["type"] == "function_call_output"
    assert payload["call_id"] == "tool-1"
    assert isinstance(payload["output"], str)
    assert json.loads(payload["output"]) == {"answer": 42}


@pytest.mark.asyncio
async def test_execute_call_section_calls_registered_function(target: OpenAIResponseTarget):
    async def add_fn(args: dict[str, Any]) -> dict[str, Any]:
        return {"sum": args["a"] + args["b"]}

    # inject registry
    target._custom_functions["add"] = add_fn

    section = {"type": "function_call", "name": "add", "arguments": json.dumps({"a": 2, "b": 3})}
    result = await target._execute_call_section(section)
    assert result == {"sum": 5}


@pytest.mark.asyncio
async def test_execute_call_section_missing_function_tolerant_mode(target: OpenAIResponseTarget):
    # default fail_on_missing_function=False
    section = {"type": "function_call", "name": "unknown_tool", "arguments": "{}"}
    result = await target._execute_call_section(section)
    assert result["error"] == "function_not_found"
    assert result["missing_function"] == "unknown_tool"
    assert "available_functions" in result


@pytest.mark.asyncio
async def test_execute_call_section_malformed_arguments_tolerant_mode(target: OpenAIResponseTarget):
    async def echo_fn(args: dict[str, Any]) -> dict[str, Any]:
        return args

    target._custom_functions["echo"] = echo_fn
    section = {"type": "function_call", "name": "echo", "arguments": "{not-json"}
    result = await target._execute_call_section(section)
    assert result["error"] == "malformed_arguments"
    assert result["function"] == "echo"
    assert result["raw_arguments"] == "{not-json"


@pytest.mark.asyncio
async def test_execute_call_section_missing_function_strict_mode(target: OpenAIResponseTarget):
    target._custom_functions = {}
    target._fail_on_missing_function = True
    section = {"type": "function_call", "name": "nope", "arguments": "{}"}
    with pytest.raises(KeyError, match="Function 'nope' is not registered"):
        await target._execute_call_section(section)


@pytest.mark.asyncio
async def test_send_prompt_async_agentic_loop_executes_function_and_returns_final_answer(target: OpenAIResponseTarget):
    # 1) Register a simple function
    async def times2(args: dict[str, Any]) -> dict[str, Any]:
        return {"result": args["x"] * 2}

    target._custom_functions["times2"] = times2

    # Create a shared conversation ID and reference piece for consistency
    shared_conversation_id = "test-conversation-123"

    # 2) Create the user prompt
    user_req = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="double 7",
                converted_value="double 7",
                original_value_data_type="text",
                converted_value_data_type="text",
                conversation_id=shared_conversation_id,
            )
        ]
    )

    # 3) Create mock SDK responses
    # First response: function_call
    first_sdk_response = MagicMock()
    first_sdk_response.status = "completed"
    first_sdk_response.error = None
    first_func_section = MagicMock()
    first_func_section.type = "function_call"
    first_func_section.call_id = "call-99"
    first_func_section.name = "times2"
    first_func_section.arguments = json.dumps({"x": 7})
    first_func_section.model_dump.return_value = {
        "type": "function_call",
        "call_id": "call-99",
        "name": "times2",
        "arguments": json.dumps({"x": 7}),
    }
    first_sdk_response.output = [first_func_section]

    # Second response: final message
    second_sdk_response = MagicMock()
    second_sdk_response.status = "completed"
    second_sdk_response.error = None
    second_msg_section = MagicMock()
    second_msg_section.type = "message"
    second_msg_section.content = [MagicMock(text="Done: 14")]
    second_sdk_response.output = [second_msg_section]

    call_counter = {"n": 0}

    # 4) Mock the SDK's create method to return first function_call, then final message
    async def mock_sdk_create(**kwargs):
        call_counter["n"] += 1
        return first_sdk_response if call_counter["n"] == 1 else second_sdk_response

    with patch.object(target._async_client.responses, "create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = mock_sdk_create

        final = await target.send_prompt_async(message=user_req)

        # Response contains all messages from the agentic loop:
        # assistant with tool call, tool output, final assistant response
        assert len(final) == 3
        # First message: assistant with function_call
        assert len(final[0].message_pieces) == 1
        assert final[0].message_pieces[0].api_role == "assistant"
        assert final[0].message_pieces[0].original_value_data_type == "function_call"
        # Second message: tool with function_call_output
        assert len(final[1].message_pieces) == 1
        assert final[1].message_pieces[0].api_role == "tool"
        assert final[1].message_pieces[0].original_value_data_type == "function_call_output"
        # Third message: final assistant response with text
        assert len(final[2].message_pieces) == 1
        assert final[2].message_pieces[0].api_role == "assistant"
        assert final[2].message_pieces[0].original_value_data_type == "text"
        assert final[2].message_pieces[0].original_value == "Done: 14"

        # Verify intermediate messages were NOT persisted to memory by the target
        # (The normalizer will handle persistence when messages are returned)
        all_messages = target._memory.get_conversation(conversation_id=shared_conversation_id)
        assert len(all_messages) == 0, (
            f"Expected 0 messages in memory (target doesn't persist), got {len(all_messages)}"
        )


def test_invalid_temperature_raises(patch_central_database):
    """Test that invalid temperature values raise PyritException."""
    with pytest.raises(PyritException, match="temperature must be between 0 and 2"):
        OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://test.com",
            api_key="test",
            temperature=-0.1,
        )

    with pytest.raises(PyritException, match="temperature must be between 0 and 2"):
        OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://test.com",
            api_key="test",
            temperature=2.1,
        )


def test_invalid_top_p_raises(patch_central_database):
    """Test that invalid top_p values raise PyritException."""
    with pytest.raises(PyritException, match="top_p must be between 0 and 1"):
        OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://test.com",
            api_key="test",
            top_p=-0.1,
        )

    with pytest.raises(PyritException, match="top_p must be between 0 and 1"):
        OpenAIResponseTarget(
            model_name="gpt-4",
            endpoint="https://test.com",
            api_key="test",
            top_p=1.1,
        )


# Unit tests for override methods


def test_check_content_filter_detects_filtered_response(target: OpenAIResponseTarget):
    """Test _check_content_filter detects content_filter error code."""
    mock_response = MagicMock()
    mock_error = MagicMock()
    mock_error.code = "content_filter"
    mock_response.error = mock_error
    mock_response.model_dump.return_value = {"error": {"code": "content_filter"}}

    assert target._check_content_filter(mock_response) is True


def test_check_content_filter_no_error(target: OpenAIResponseTarget):
    """Test _check_content_filter returns False when no error."""
    mock_response = MagicMock()
    mock_response.error = None

    assert target._check_content_filter(mock_response) is False


def test_check_content_filter_different_error(target: OpenAIResponseTarget):
    """Test _check_content_filter returns False for non-content-filter errors."""
    mock_response = MagicMock()
    mock_error = MagicMock()
    mock_error.code = "rate_limit"
    mock_response.error = mock_error
    mock_response.model_dump.return_value = {"error": {"code": "rate_limit"}}

    assert target._check_content_filter(mock_response) is False


def test_validate_response_success(target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response passes for valid completed response."""
    mock_response = MagicMock()
    mock_response.error = None
    mock_response.status = "completed"
    mock_response.output = [{"type": "message", "content": [{"text": "Hello"}]}]

    result = target._validate_response(mock_response, dummy_text_message_piece)
    assert result is None


def test_validate_response_non_content_filter_error(
    target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece
):
    """Test _validate_response raises for non-content-filter errors."""
    mock_response = MagicMock()
    mock_error = MagicMock()
    mock_error.code = "invalid_request"
    mock_error.message = "Invalid request parameters"
    mock_response.error = mock_error
    mock_response.status = "completed"

    with pytest.raises(PyritException, match="Response error: invalid_request"):
        target._validate_response(mock_response, dummy_text_message_piece)


def test_validate_response_invalid_status(target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response raises for non-completed status."""
    mock_response = MagicMock()
    mock_response.error = None
    mock_response.status = "failed"
    mock_response.output = []

    with pytest.raises(PyritException, match="Unexpected status: failed"):
        target._validate_response(mock_response, dummy_text_message_piece)


def test_validate_response_empty_output(target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece):
    """Test _validate_response raises for empty output."""
    mock_response = MagicMock()
    mock_response.error = None
    mock_response.status = "completed"
    mock_response.output = []

    with pytest.raises(EmptyResponseException, match="empty response"):
        target._validate_response(mock_response, dummy_text_message_piece)


@pytest.mark.asyncio
async def test_construct_message_from_response(target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece):
    """Test _construct_message_from_response parses output sections."""
    mock_response = MagicMock()
    mock_response.output = [{"type": "message", "content": [{"type": "text", "text": "Hello from Response API"}]}]

    # Mock the _parse_response_output_section method
    with patch.object(target, "_parse_response_output_section") as mock_parse:
        mock_piece = MessagePiece(
            role="assistant",
            original_value="Hello from Response API",
            converted_value="Hello from Response API",
            conversation_id=dummy_text_message_piece.conversation_id,
        )
        mock_parse.return_value = mock_piece

        result = await target._construct_message_from_response(mock_response, dummy_text_message_piece)

        assert isinstance(result, Message)
        assert len(result.message_pieces) == 1
        mock_parse.assert_called_once()


# ── Reasoning effort / summary tests ───────────────────────────────────────


def test_init_with_reasoning_effort(patch_central_database):
    target = OpenAIResponseTarget(
        model_name="gpt-5",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        reasoning_effort="high",
    )
    assert target._reasoning_effort == "high"


def test_init_with_reasoning_summary(patch_central_database):
    target = OpenAIResponseTarget(
        model_name="gpt-5",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        reasoning_summary="auto",
    )
    assert target._reasoning_summary == "auto"


def test_init_with_reasoning_effort_and_summary(patch_central_database):
    target = OpenAIResponseTarget(
        model_name="gpt-5",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        reasoning_effort="low",
        reasoning_summary="detailed",
    )
    assert target._reasoning_effort == "low"
    assert target._reasoning_summary == "detailed"


def test_init_without_reasoning_params(patch_central_database):
    target = OpenAIResponseTarget(
        model_name="gpt-5",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
    )
    assert target._reasoning_effort is None
    assert target._reasoning_summary is None


@pytest.mark.asyncio
async def test_construct_request_body_includes_reasoning_effort(
    patch_central_database, dummy_text_message_piece: MessagePiece
):
    target = OpenAIResponseTarget(
        model_name="gpt-5",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        reasoning_effort="medium",
    )
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)
    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert body["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_construct_request_body_includes_reasoning_summary(
    patch_central_database, dummy_text_message_piece: MessagePiece
):
    target = OpenAIResponseTarget(
        model_name="gpt-5",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        reasoning_summary="detailed",
    )
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)
    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert body["reasoning"] == {"summary": "detailed"}


@pytest.mark.asyncio
async def test_construct_request_body_includes_reasoning_effort_and_summary(
    patch_central_database, dummy_text_message_piece: MessagePiece
):
    target = OpenAIResponseTarget(
        model_name="gpt-5",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        reasoning_effort="high",
        reasoning_summary="auto",
    )
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)
    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert body["reasoning"] == {"effort": "high", "summary": "auto"}


@pytest.mark.asyncio
async def test_construct_request_body_omits_reasoning_when_not_set(
    target: OpenAIResponseTarget, dummy_text_message_piece: MessagePiece
):
    request = Message(message_pieces=[dummy_text_message_piece])
    jrc = _JsonResponseConfig.from_metadata(metadata=None)
    body = await target._construct_request_body(conversation=[request], json_config=jrc)
    assert "reasoning" not in body


def test_build_identifier_includes_reasoning_params(patch_central_database):
    target = OpenAIResponseTarget(
        model_name="gpt-5",
        endpoint="https://mock.azure.com/",
        api_key="mock-api-key",
        reasoning_effort="low",
        reasoning_summary="concise",
    )
    identifier = target._build_identifier()
    assert identifier.params["reasoning_effort"] == "low"
    assert identifier.params["reasoning_summary"] == "concise"


def test_get_identifier_ignores_underlying_model_env_var_when_model_name_explicit(patch_central_database):
    """Test that underlying_model env var is NOT used when model_name is explicitly passed."""
    with patch.dict(os.environ, {"OPENAI_RESPONSES_UNDERLYING_MODEL": "gpt-4o"}):
        target = OpenAIResponseTarget(
            model_name="gpt-4.1",
            endpoint="https://mock.azure.com/",
            api_key="mock-api-key",
        )

        identifier = target.get_identifier()

        # model_name was explicit, so underlying_model env var should be ignored
        assert identifier.params["model_name"] == "gpt-4.1"
        assert identifier.params["underlying_model_name"] == ""
