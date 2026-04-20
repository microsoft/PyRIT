# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import uuid
from collections.abc import MutableSequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_audio_message_piece, get_image_message_piece, get_sample_conversations

from pyrit.exceptions.exception_classes import (
    EmptyResponseException,
    RateLimitException,
)
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import OpenAIImageTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


@pytest.fixture
def image_target(patch_central_database) -> OpenAIImageTarget:
    return OpenAIImageTarget(
        model_name="dall-e-3",
        endpoint="test",
        api_key="test",
        custom_configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=False,
                supports_multi_message_pieces=True,
                input_modalities=frozenset(
                    {
                        frozenset(["text"]),
                        frozenset(["text", "image_path"]),
                    }
                ),
                output_modalities=frozenset({frozenset(["image_path"])}),
            )
        ),
    )


@pytest.fixture
def image_response_json() -> dict:
    return {
        "data": [
            {
                "b64_json": "aGVsbG8=",
            }
        ],
        "model": "dall-e-3",
    }


@pytest.fixture
def sample_conversations() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


def test_initialization_with_required_parameters(image_target: OpenAIImageTarget):
    assert image_target
    assert image_target._model_name == "dall-e-3"


@pytest.mark.asyncio
async def test_send_prompt_async_generate(
    image_target: OpenAIImageTarget,
    sample_conversations: MutableSequence[MessagePiece],
    image_response_json: dict,
):
    request = sample_conversations[0]

    # Mock SDK response
    mock_response = MagicMock()
    mock_image = MagicMock()
    mock_image.b64_json = "aGVsbG8="  # Base64 encoded "hello"
    mock_response.data = [mock_image]

    with patch.object(image_target._async_client.images, "generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_response

        resp = await image_target.send_prompt_async(message=Message([request]))
        assert len(resp) == 1
        assert resp
        path = resp[0].message_pieces[0].original_value
        assert os.path.isfile(path)

        with open(path, "rb") as file:
            data = file.read()
            assert data == b"hello"

        os.remove(path)


@pytest.mark.asyncio
async def test_send_prompt_async_edit(
    image_target: OpenAIImageTarget,
):
    image_piece = get_image_message_piece()
    text_piece = MessagePiece(
        role="user",
        conversation_id=image_piece.conversation_id,
        original_value="edit this image",
        converted_value="edit this image",
        original_value_data_type="text",
        converted_value_data_type="text",
    )

    # Mock SDK response
    mock_response = MagicMock()
    mock_image = MagicMock()
    mock_image.b64_json = "aGVsbG8="  # Base64 encoded "hello"
    mock_response.data = [mock_image]

    with patch.object(image_target._async_client.images, "edit", new_callable=AsyncMock) as mock_edit:
        mock_edit.return_value = mock_response

        resp = await image_target.send_prompt_async(message=Message([text_piece, image_piece]))
        assert len(resp) == 1
        assert resp
        path = resp[0].message_pieces[0].original_value
        assert os.path.isfile(path)

        with open(path, "rb") as file:
            data = file.read()
            assert data == b"hello"

        os.remove(path)

    os.remove(image_piece.original_value)


@pytest.mark.asyncio
async def test_send_prompt_async_edit_multiple_images(
    image_target: OpenAIImageTarget,
):
    image_piece = get_image_message_piece()
    image_pieces = [image_piece for _ in range(OpenAIImageTarget._MAX_INPUT_IMAGES - 1)]
    text_piece = MessagePiece(
        role="user",
        conversation_id=image_piece.conversation_id,
        original_value="edit this image",
        converted_value="edit this image",
        original_value_data_type="text",
        converted_value_data_type="text",
    )

    # Mock SDK response
    mock_response = MagicMock()
    mock_image = MagicMock()
    mock_image.b64_json = "aGVsbG8="  # Base64 encoded "hello"
    mock_response.data = [mock_image]

    with patch.object(image_target._async_client.images, "edit", new_callable=AsyncMock) as mock_edit:
        mock_edit.return_value = mock_response

        resp = await image_target.send_prompt_async(message=Message([image_piece, text_piece] + image_pieces))
        assert len(resp) == 1
        assert resp
        path = resp[0].message_pieces[0].original_value
        assert os.path.isfile(path)

        with open(path, "rb") as file:
            data = file.read()
            assert data == b"hello"

        os.remove(path)

    os.remove(image_piece.original_value)


@pytest.mark.asyncio
async def test_send_prompt_async_invalid_image_path(
    image_target: OpenAIImageTarget,
):
    invalid_path = os.path.join(os.getcwd(), "does_not_exist.png")
    text_piece = MessagePiece(
        role="user",
        conversation_id="123",
        original_value="edit this image",
        converted_value="edit this image",
        original_value_data_type="text",
        converted_value_data_type="text",
    )
    image_piece = MessagePiece(
        role="user",
        conversation_id="123",
        original_value=invalid_path,
        converted_value=invalid_path,
        original_value_data_type="image_path",
        converted_value_data_type="image_path",
    )

    with pytest.raises(FileNotFoundError):
        await image_target.send_prompt_async(message=Message([text_piece, image_piece]))


@pytest.mark.asyncio
async def test_send_prompt_async_empty_response(
    image_target: OpenAIImageTarget,
    sample_conversations: MutableSequence[MessagePiece],
    image_response_json: dict,
):
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Mock SDK response with empty b64_json and no URL
    mock_response = MagicMock()
    mock_image = MagicMock()
    mock_image.b64_json = ""  # Empty response
    mock_image.url = None  # No URL either
    mock_response.data = [mock_image]

    with patch.object(image_target._async_client.images, "generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_response

        with pytest.raises(EmptyResponseException):
            await image_target.send_prompt_async(message=Message([request]))


@pytest.mark.asyncio
async def test_send_prompt_async_rate_limit_exception(
    image_target: OpenAIImageTarget, sample_conversations: MutableSequence[MessagePiece]
):
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Import SDK exception
    from openai import RateLimitError

    with patch.object(image_target._async_client.images, "generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = RateLimitError("Rate Limit Reached", response=MagicMock(), body={})

        with pytest.raises(RateLimitException):
            await image_target.send_prompt_async(message=Message([request]))


@pytest.mark.asyncio
async def test_send_prompt_async_bad_request_error(
    image_target: OpenAIImageTarget, sample_conversations: MutableSequence[MessagePiece]
):
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Import SDK exception
    from openai import BadRequestError

    mock_response = MagicMock()
    mock_response.text = '{"error": {"message": "Bad Request Error"}}'

    # Create exception with proper status_code
    bad_request_error = BadRequestError(
        "Bad Request Error", response=mock_response, body={"error": {"message": "Bad Request Error"}}
    )
    bad_request_error.status_code = 400

    with patch.object(image_target._async_client.images, "generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = bad_request_error

        # Non-content-filter BadRequestError should be re-raised (same as chat target behavior)
        with pytest.raises(Exception):  # noqa: B017
            await image_target.send_prompt_async(message=Message([request]))


@pytest.mark.asyncio
async def test_send_prompt_async_empty_response_adds_memory(
    image_target: OpenAIImageTarget,
    sample_conversations: MutableSequence[MessagePiece],
) -> None:
    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = []
    mock_memory.add_message_to_memory = AsyncMock()

    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Mock SDK response with empty b64_json and no URL
    mock_response = MagicMock()
    mock_image = MagicMock()
    mock_image.b64_json = ""  # Empty response
    mock_image.url = None  # No URL either
    mock_response.data = [mock_image]

    with patch.object(image_target._async_client.images, "generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_response
        image_target._memory = mock_memory

        with pytest.raises(EmptyResponseException):
            await image_target.send_prompt_async(message=Message([request]))


@pytest.mark.asyncio
async def test_send_prompt_async_rate_limit_adds_memory(
    image_target: OpenAIImageTarget,
    sample_conversations: MutableSequence[MessagePiece],
) -> None:
    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = []
    mock_memory.add_message_to_memory = AsyncMock()

    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Import SDK exception
    from openai import RateLimitError

    with patch.object(image_target._async_client.images, "generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = RateLimitError("Rate Limit Reached", response=MagicMock(), body={})
        image_target._memory = mock_memory

        with pytest.raises(RateLimitException):
            await image_target.send_prompt_async(message=Message([request]))


@pytest.mark.asyncio
async def test_send_prompt_async_bad_request_content_filter(
    image_target: OpenAIImageTarget,
    sample_conversations: MutableSequence[MessagePiece],
) -> None:
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Import SDK exception
    from openai import BadRequestError

    mock_response = MagicMock()
    mock_response.text = '{"error": {"code": "content_filter", "message": "Content filtered"}}'

    # Create exception with proper status_code
    bad_request_error = BadRequestError(
        "Bad Request Error",
        response=mock_response,
        body={"error": {"code": "content_filter", "message": "Content filtered"}},
    )
    bad_request_error.status_code = 400

    with patch.object(image_target._async_client.images, "generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = bad_request_error
        result = await image_target.send_prompt_async(message=Message([request]))
        assert len(result) == 1
        assert result[0].message_pieces[0].converted_value_data_type == "error"
        assert "content_filter" in result[0].message_pieces[0].converted_value


@pytest.mark.asyncio
async def test_send_prompt_async_bad_request_content_policy_violation(
    image_target: OpenAIImageTarget,
    sample_conversations: MutableSequence[MessagePiece],
) -> None:
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Import SDK exception
    from openai import BadRequestError

    mock_response = MagicMock()
    mock_response.text = '{"error": {"code": "content_policy_violation", "message": "Content blocked by policy"}}'

    # Create exception with proper status_code and inner_error structure
    bad_request_error = BadRequestError(
        "Content blocked by policy",
        response=mock_response,
        body={"error": {"code": "content_policy_violation", "message": "Content blocked by policy"}},
    )
    bad_request_error.status_code = 400

    with patch.object(image_target._async_client.images, "generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = bad_request_error
        result = await image_target.send_prompt_async(message=Message([request]))
        assert len(result) == 1
        assert result[0].message_pieces[0].response_error == "blocked"
        assert result[0].message_pieces[0].converted_value_data_type == "error"


@pytest.mark.asyncio
async def test_send_prompt_async_url_response_downloads_image(
    image_target: OpenAIImageTarget,
    sample_conversations: MutableSequence[MessagePiece],
):
    """Test that when model returns URL instead of base64, the image is downloaded from URL."""
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Response returns URL (no b64_json)
    mock_response_url = MagicMock()
    mock_image_url = MagicMock()
    mock_image_url.b64_json = None
    mock_image_url.url = "https://example.com/image.png"
    mock_response_url.data = [mock_image_url]

    # Mock httpx response for URL download
    mock_http_response = MagicMock()
    mock_http_response.content = b"hello"
    mock_http_response.raise_for_status = MagicMock()

    with patch.object(image_target._async_client.images, "generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_response_url

        with patch("pyrit.prompt_target.openai.openai_image_target.httpx.AsyncClient") as mock_httpx:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_http_response)
            mock_httpx.return_value.__aenter__.return_value = mock_client_instance

            resp = await image_target.send_prompt_async(message=Message([request]))

            # Should have called generate once
            assert mock_generate.call_count == 1

            # Should have downloaded from the URL
            mock_client_instance.get.assert_called_once_with("https://example.com/image.png")

            # Should have successfully returned the image
            assert len(resp) == 1
            path = resp[0].message_pieces[0].original_value
            assert os.path.isfile(path)

            with open(path, "rb") as file:
                data = file.read()
                assert data == b"hello"

            os.remove(path)


@pytest.mark.asyncio
async def test_validate_no_text_piece(image_target: OpenAIImageTarget):
    image_piece = get_image_message_piece()

    try:
        request = Message(message_pieces=[image_piece])
        with pytest.raises(ValueError, match="The message must contain exactly one text piece."):
            await image_target.send_prompt_async(message=request)
    finally:
        if os.path.isfile(image_piece.original_value):
            os.remove(image_piece.original_value)


@pytest.mark.asyncio
async def test_validate_multiple_text_pieces(image_target: OpenAIImageTarget):
    request = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                conversation_id="123",
                original_value="test",
                converted_value="test",
                original_value_data_type="text",
                converted_value_data_type="text",
            ),
            MessagePiece(
                role="user",
                conversation_id="123",
                original_value="test2",
                converted_value="test2",
                original_value_data_type="text",
                converted_value_data_type="text",
            ),
        ]
    )

    with pytest.raises(ValueError, match="The message must contain exactly one text piece."):
        await image_target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_validate_image_pieces(image_target: OpenAIImageTarget):
    image_piece = get_image_message_piece()
    image_pieces = [image_piece for _ in range(OpenAIImageTarget._MAX_INPUT_IMAGES + 1)]
    text_piece = MessagePiece(
        role="user",
        conversation_id=image_piece.conversation_id,
        original_value="test",
        converted_value="test",
        original_value_data_type="text",
        converted_value_data_type="text",
    )

    try:
        request = Message(message_pieces=image_pieces + [text_piece])
        with pytest.raises(
            ValueError,
            match=f"The message can contain up to {OpenAIImageTarget._MAX_INPUT_IMAGES} image pieces.",
        ):
            await image_target.send_prompt_async(message=request)
    finally:
        if os.path.isfile(image_piece.original_value):
            os.remove(image_piece.original_value)


@pytest.mark.asyncio
async def test_validate_piece_type(image_target: OpenAIImageTarget):
    audio_piece = get_audio_message_piece()
    text_piece = MessagePiece(
        role="user",
        conversation_id=audio_piece.conversation_id,
        original_value="test",
        converted_value="test",
        original_value_data_type="text",
        converted_value_data_type="text",
    )

    try:
        request = Message(message_pieces=[audio_piece, text_piece])
        with pytest.raises(
            ValueError,
            match="This target supports only the following data types",
        ):
            await image_target.send_prompt_async(message=request)
    finally:
        if os.path.isfile(audio_piece.original_value):
            os.remove(audio_piece.original_value)


@pytest.mark.asyncio
async def test_validate_previous_conversations(
    image_target: OpenAIImageTarget, sample_conversations: MutableSequence[MessagePiece]
):
    message_piece = sample_conversations[0]

    prior_message = Message(message_pieces=[message_piece])

    mock_memory = MagicMock()
    mock_memory.get_conversation.return_value = [prior_message]
    mock_memory.add_message_to_memory = AsyncMock()

    image_target._memory = mock_memory

    request = Message(message_pieces=[message_piece])

    with pytest.raises(
        ValueError,
        match="This target only supports a single turn conversation.*If your target does support this, set the"
        " custom_configuration parameter accordingly",
    ):
        await image_target.send_prompt_async(message=request)
