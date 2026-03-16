# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import uuid
from collections.abc import MutableSequence
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import BadRequestError, RateLimitError
from unit.mocks import get_image_message_piece, get_sample_conversations

from pyrit.exceptions import RateLimitException
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import OpenAITTSTarget
from pyrit.prompt_target.openai.openai_tts_target import TTSResponseFormat


@pytest.fixture
def sample_conversations() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


@pytest.fixture
def tts_target(patch_central_database) -> OpenAITTSTarget:
    return OpenAITTSTarget(model_name="test", endpoint="https://test.com", api_key="test")


def test_tts_initializes(tts_target: OpenAITTSTarget):
    assert tts_target


def test_tts_initializes_calls_get_required_parameters(patch_central_database):
    with (
        patch("pyrit.common.default_values.get_required_value") as mock_get_required,
        patch("pyrit.common.default_values.get_non_required_value") as mock_get_non_required,
    ):
        mock_get_required.side_effect = lambda env_var_name, passed_value: passed_value
        mock_get_non_required.side_effect = lambda env_var_name, passed_value: passed_value

        target = OpenAITTSTarget(
            model_name="deploymenttest",
            endpoint="endpointtest",
            api_key="keytest",
        )

        assert mock_get_required.call_count == 2

        mock_get_required.assert_any_call(
            env_var_name=target.endpoint_environment_variable, passed_value="endpointtest"
        )
        mock_get_required.assert_any_call(
            env_var_name=target.model_name_environment_variable, passed_value="deploymenttest"
        )
        mock_get_non_required.assert_any_call(env_var_name=target.api_key_environment_variable, passed_value="keytest")


@pytest.mark.asyncio
async def test_tts_validate_request_length(tts_target: OpenAITTSTarget):
    request = Message(
        message_pieces=[
            MessagePiece(role="user", conversation_id="123", original_value="test"),
            MessagePiece(role="user", conversation_id="123", original_value="test2"),
        ]
    )
    with pytest.raises(ValueError, match="This target only supports a single message piece."):
        await tts_target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_tts_validate_prompt_type(tts_target: OpenAITTSTarget):
    request = Message(message_pieces=[get_image_message_piece()])
    with pytest.raises(ValueError, match="This target only supports text prompt input."):
        await tts_target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_tts_validate_previous_conversations(
    tts_target: OpenAITTSTarget, sample_conversations: MutableSequence[MessagePiece]
):
    message_piece = sample_conversations[0]

    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = sample_conversations
    mock_memory.add_message_to_memory = AsyncMock()

    tts_target._memory = mock_memory

    request = Message(message_pieces=[message_piece])

    with patch("pyrit.common.net_utility.make_request_and_raise_if_error_async") as mock_request:
        mock_request.return_value = MagicMock(content=b"audio data")
        with pytest.raises(ValueError, match="This target only supports a single turn conversation."):
            await tts_target.send_prompt_async(message=request)


@pytest.mark.parametrize("response_format", ["mp3", "ogg"])
@pytest.mark.asyncio
async def test_tts_send_prompt_file_save_async(
    patch_central_database,
    sample_conversations: MutableSequence[MessagePiece],
    response_format: TTSResponseFormat,
) -> None:
    tts_target = OpenAITTSTarget(model_name="test", endpoint="test", api_key="test", response_format=response_format)

    message_piece = sample_conversations[0]
    message_piece.conversation_id = str(uuid.uuid4())
    request = Message(message_pieces=[message_piece])

    # Mock SDK response
    mock_audio_response = MagicMock()
    mock_audio_response.content = b"audio data"

    with patch.object(tts_target._async_client.audio.speech, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_audio_response
        response = await tts_target.send_prompt_async(message=request)
        assert len(response) == 1

        file_path = response[0].get_value()
        assert file_path
        assert file_path.endswith(f".{response_format}")
        assert os.path.exists(file_path)
        data = Path(file_path).read_bytes()
        assert data == b"audio data"
        os.remove(file_path)


testdata = [(400, "Bad Request", Exception), (429, "Rate Limit Reached", RateLimitException)]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code, error_text, exception_class", testdata)
async def test_tts_send_prompt_async_exception_adds_to_memory(
    tts_target: OpenAITTSTarget,
    sample_conversations: MutableSequence[MessagePiece],
    status_code: int,
    error_text: str,
    exception_class: type[BaseException],
):
    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = []
    mock_memory.add_message_to_memory = AsyncMock()

    tts_target._memory = mock_memory

    message_piece = sample_conversations[0]
    message_piece.conversation_id = str(uuid.uuid4())
    request = Message(message_pieces=[message_piece])

    # Create appropriate SDK exception
    mock_response = MagicMock()
    mock_response.text = error_text

    sdk_exception: Exception
    if status_code == 400:
        sdk_exception = BadRequestError(error_text, response=mock_response, body={})
    else:  # 429
        sdk_exception = RateLimitError(error_text, response=mock_response, body={})

    with patch.object(tts_target._async_client.audio.speech, "create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = sdk_exception

        with pytest.raises(exception_class):
            await tts_target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_tts_send_prompt_async_rate_limit_exception_retries(
    tts_target: OpenAITTSTarget, sample_conversations: MutableSequence[MessagePiece]
):
    mock_response = MagicMock()
    mock_response.text = "Rate Limit Reached"
    sdk_exception = RateLimitError("Rate Limit Reached", response=mock_response, body={})

    with patch.object(tts_target._async_client.audio.speech, "create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = sdk_exception

        message_piece = sample_conversations[0]
        request = Message(message_pieces=[message_piece])

        with pytest.raises(RateLimitException):
            await tts_target.send_prompt_async(message=request)


def test_is_json_response_supported(tts_target: OpenAITTSTarget):
    assert tts_target.is_json_response_supported() is False


@pytest.mark.asyncio
async def test_tts_send_prompt_with_speed_parameter(
    patch_central_database,
    sample_conversations: MutableSequence[MessagePiece],
) -> None:
    tts_target = OpenAITTSTarget(model_name="test", endpoint="test", api_key="test", speed=1.5)

    message_piece = sample_conversations[0]
    message_piece.conversation_id = str(uuid.uuid4())
    request = Message(message_pieces=[message_piece])

    # Mock SDK response
    mock_audio_response = MagicMock()
    mock_audio_response.content = b"audio data"

    with patch.object(tts_target._async_client.audio.speech, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_audio_response
        response = await tts_target.send_prompt_async(message=request)

        # Verify speed parameter was passed
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["speed"] == 1.5

        # Verify audio was saved
        assert len(response) == 1
        file_path = response[0].get_value()
        assert file_path
        assert os.path.exists(file_path)
        os.remove(file_path)
