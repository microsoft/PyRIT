# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from collections.abc import MutableSequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_sample_conversations

from pyrit.exceptions import RateLimitException
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import OpenAIVideoTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

_VIDEO_PATH_CONFIGURATION = TargetConfiguration(
    capabilities=TargetCapabilities(
        supports_multi_turn=False,
        supports_multi_message_pieces=True,
        input_modalities=frozenset(
            {
                frozenset(["text"]),
                frozenset(["text", "image_path"]),
                frozenset(["text", "video_path"]),
            }
        ),
        output_modalities=frozenset({frozenset(["video_path"])}),
    )
)


@pytest.fixture
def sample_conversations() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


@pytest.fixture
def video_target(patch_central_database) -> OpenAIVideoTarget:
    return OpenAIVideoTarget(
        endpoint="https://api.openai.com/v1",
        api_key="test",
        model_name="sora-2",
    )


def test_video_initializes(video_target: OpenAIVideoTarget):
    assert video_target
    assert video_target._model_name == "sora-2"


def test_video_initialization_invalid_resolution(patch_central_database):
    with pytest.raises(ValueError, match="Invalid resolution"):
        OpenAIVideoTarget(
            model_name="gpt-4",
            endpoint="https://api.openai.com/v1",
            api_key="test",
            resolution_dimensions="invalid",
        )


def test_video_initialization_invalid_duration(patch_central_database):
    with pytest.raises(ValueError, match="Invalid duration"):
        OpenAIVideoTarget(
            model_name="gpt-4",
            endpoint="https://api.openai.com/v1",
            api_key="test",
            n_seconds=20,  # Only 4, 8, 12 are supported
        )


def test_video_validate_request_multiple_text_pieces(video_target: OpenAIVideoTarget):
    """Test validation rejects multiple text pieces."""
    with pytest.raises(ValueError, match="Expected exactly 1 text piece"):
        conversation_id = str(uuid.uuid4())
        msg1 = MessagePiece(
            role="user", original_value="test1", converted_value="test1", conversation_id=conversation_id
        )
        msg2 = MessagePiece(
            role="user", original_value="test2", converted_value="test2", conversation_id=conversation_id
        )
        video_target._validate_request(message=Message([msg1, msg2]))


def test_video_validate_prompt_type_image_only(video_target: OpenAIVideoTarget):
    """Test validation rejects image-only input (must have text)."""
    with pytest.raises(ValueError, match="Expected exactly 1 text piece"):
        msg = MessagePiece(
            role="user", original_value="test", converted_value="test", converted_value_data_type="image_path"
        )
        video_target._validate_request(message=Message([msg]))


@pytest.mark.asyncio
async def test_video_send_prompt_async_success(
    video_target: OpenAIVideoTarget, sample_conversations: MutableSequence[MessagePiece]
):
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Mock successful video generation
    mock_video = MagicMock()
    mock_video.id = "video_123"
    mock_video.status = "completed"
    mock_video.error = None

    # Mock video content as HttpxBinaryResponseContent
    mock_video_response = MagicMock()
    mock_video_response.content = b"video data content"

    # Mock data serializer
    mock_serializer = MagicMock()
    mock_serializer.value = "/path/to/video.mp4"
    mock_serializer.save_data = AsyncMock()

    with (
        patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create,
        patch.object(video_target._async_client.videos, "download_content", new_callable=AsyncMock) as mock_download,
        patch("pyrit.prompt_target.openai.openai_video_target.data_serializer_factory") as mock_factory,
    ):
        mock_create.return_value = mock_video
        mock_download.return_value = mock_video_response
        mock_factory.return_value = mock_serializer

        response = await video_target.send_prompt_async(message=Message([request]))

        # Verify SDK methods were called correctly
        mock_create.assert_called_once_with(
            model="sora-2",
            prompt="Hello, how are you?",
            size="1280x720",
            seconds="4",
        )
        mock_download.assert_called_once_with("video_123")
        mock_serializer.save_data.assert_called_once_with(data=b"video data content")

        # Verify response
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].converted_value == "/path/to/video.mp4"
        assert response[0].message_pieces[0].converted_value_data_type == "video_path"


@pytest.mark.asyncio
async def test_video_send_prompt_async_failed_content_filter(
    video_target: OpenAIVideoTarget, sample_conversations: MutableSequence[MessagePiece]
):
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Mock failed video generation with output-side content filter
    mock_video = MagicMock()
    mock_video.id = "video_456"
    mock_video.status = "failed"
    mock_error = MagicMock()
    mock_error.code = "content_filter"
    mock_video.error = mock_error
    video_response_dict = {
        "id": "video_456",
        "status": "failed",
        "error": {"code": "content_filter"},
    }
    mock_video.model_dump.return_value = video_response_dict
    mock_video.model_dump_json.return_value = json.dumps(video_response_dict)

    with patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_video

        response = await video_target.send_prompt_async(message=Message([request]))

        # Verify response is error with blocked status
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].response_error == "blocked"
        assert (
            "content_filter" in response[0].message_pieces[0].converted_value.lower()
            or "blocked" in response[0].message_pieces[0].converted_value.lower()
        )


@pytest.mark.asyncio
async def test_video_send_prompt_async_failed_processing_error(
    video_target: OpenAIVideoTarget, sample_conversations: MutableSequence[MessagePiece]
):
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Mock failed video generation with processing error
    mock_video = MagicMock()
    mock_video.id = "video_789"
    mock_video.status = "failed"
    mock_error = MagicMock()
    mock_error.code = "internal_error"
    mock_video.error = mock_error

    with patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_video

        response = await video_target.send_prompt_async(message=Message([request]))

        # Verify response is processing error
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].response_error == "processing"


@pytest.mark.asyncio
async def test_video_send_prompt_async_bad_request_exception(
    video_target: OpenAIVideoTarget, sample_conversations: MutableSequence[MessagePiece]
):
    from openai import BadRequestError

    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Mock BadRequestError with content filter
    mock_response = MagicMock()
    mock_response.text = '{"error": {"code": "content_policy_violation", "message": "Content blocked"}}'

    bad_request_error = BadRequestError(
        "Content blocked",
        response=mock_response,
        body={"error": {"code": "content_policy_violation", "message": "Content blocked"}},
    )
    bad_request_error.status_code = 400

    with patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = bad_request_error

        response = await video_target.send_prompt_async(message=Message([request]))

        # Verify response is error with blocked status (content filter)
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].response_error == "blocked"


@pytest.mark.asyncio
async def test_video_send_prompt_async_rate_limit_exception(
    video_target: OpenAIVideoTarget, sample_conversations: MutableSequence[MessagePiece]
):
    from openai import RateLimitError

    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Mock RateLimitError
    mock_response = MagicMock()
    mock_response.text = "Rate limit exceeded"

    rate_limit_error = RateLimitError("Rate limit exceeded", response=mock_response, body={})

    with patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = rate_limit_error

        with pytest.raises(RateLimitException):
            await video_target.send_prompt_async(message=Message([request]))


@pytest.mark.asyncio
async def test_video_send_prompt_async_api_error(
    video_target: OpenAIVideoTarget, sample_conversations: MutableSequence[MessagePiece]
):
    from openai import APIStatusError

    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Mock APIStatusError
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal server error"

    api_error = APIStatusError("Internal server error", response=mock_response, body={})

    with patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = api_error

        with pytest.raises(APIStatusError):
            await video_target.send_prompt_async(message=Message([request]))


@pytest.mark.asyncio
async def test_video_send_prompt_async_unexpected_status(
    video_target: OpenAIVideoTarget, sample_conversations: MutableSequence[MessagePiece]
):
    request = sample_conversations[0]
    request.conversation_id = str(uuid.uuid4())

    # Mock video with unexpected status
    mock_video = MagicMock()
    mock_video.id = "video_unexpected"
    mock_video.status = "pending"  # Unexpected status
    mock_video.error = None

    with patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_video

        response = await video_target.send_prompt_async(message=Message([request]))

        # Verify response is error with unknown status
        assert len(response) == 1
        assert len(response[0].message_pieces) == 1
        assert response[0].message_pieces[0].response_error == "unknown"
        assert "unexpected status: pending" in response[0].message_pieces[0].converted_value


# Unit tests for override methods


def test_check_content_filter_detects_output_content_filter(video_target: OpenAIVideoTarget):
    """Test _check_content_filter detects output-side content_filter error."""
    mock_video = MagicMock()
    mock_video.status = "failed"
    mock_error = MagicMock()
    mock_error.code = "content_filter"
    mock_video.error = mock_error
    mock_video.model_dump.return_value = {"error": {"code": "content_filter"}}

    assert video_target._check_content_filter(mock_video) is True


def test_check_content_filter_detects_moderation_blocked(video_target: OpenAIVideoTarget):
    """Test _check_content_filter detects moderation_blocked error."""
    mock_video = MagicMock()
    mock_video.status = "failed"
    mock_error = MagicMock()
    mock_error.code = "moderation_blocked"
    mock_video.error = mock_error
    mock_video.model_dump.return_value = {"error": {"code": "moderation_blocked"}}

    assert video_target._check_content_filter(mock_video) is True


def test_check_content_filter_completed_status(video_target: OpenAIVideoTarget):
    """Test _check_content_filter returns False for completed videos."""
    mock_video = MagicMock()
    mock_video.status = "completed"
    mock_video.error = None

    assert video_target._check_content_filter(mock_video) is False


def test_check_content_filter_different_error(video_target: OpenAIVideoTarget):
    """Test _check_content_filter returns False for non-content-filter errors."""
    mock_video = MagicMock()
    mock_video.status = "failed"
    mock_error = MagicMock()
    mock_error.code = "processing_error"
    mock_video.error = mock_error
    mock_video.model_dump.return_value = {"error": {"code": "processing_error"}}

    assert video_target._check_content_filter(mock_video) is False


def test_check_content_filter_no_error_object(video_target: OpenAIVideoTarget):
    """Test _check_content_filter returns False when no error object."""
    mock_video = MagicMock()
    mock_video.status = "failed"
    mock_video.error = None

    assert video_target._check_content_filter(mock_video) is False


# Tests for image-to-video and remix features


class TestVideoTargetValidation:
    """Tests for video target validation with new features."""

    def test_validate_accepts_text_only(self, video_target: OpenAIVideoTarget):
        """Test validation accepts single text piece (text-to-video mode)."""
        msg = MessagePiece(role="user", original_value="test prompt", converted_value="test prompt")
        # Should not raise
        video_target._validate_request(message=Message([msg]))

    def test_validate_accepts_text_and_image(self, video_target: OpenAIVideoTarget):
        """Test validation accepts text + image (image-to-video mode)."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="animate this",
            converted_value="animate this",
            conversation_id=conversation_id,
        )
        msg_image = MessagePiece(
            role="user",
            original_value="/path/image.png",
            converted_value="/path/image.png",
            converted_value_data_type="image_path",
            conversation_id=conversation_id,
        )
        # Should not raise
        video_target._validate_request(message=Message([msg_text, msg_image]))

    def test_validate_rejects_multiple_images(self, video_target: OpenAIVideoTarget):
        """Test validation rejects multiple image pieces."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="animate",
            converted_value="animate",
            conversation_id=conversation_id,
        )
        msg_img1 = MessagePiece(
            role="user",
            original_value="/path/img1.png",
            converted_value="/path/img1.png",
            converted_value_data_type="image_path",
            conversation_id=conversation_id,
        )
        msg_img2 = MessagePiece(
            role="user",
            original_value="/path/img2.png",
            converted_value="/path/img2.png",
            converted_value_data_type="image_path",
            conversation_id=conversation_id,
        )
        with pytest.raises(ValueError, match="at most 1 image piece"):
            video_target._validate_request(message=Message([msg_text, msg_img1, msg_img2]))

    def test_validate_rejects_unsupported_types(self, video_target: OpenAIVideoTarget):
        """Test validation rejects unsupported data types."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="test",
            converted_value="test",
            conversation_id=conversation_id,
        )
        msg_audio = MessagePiece(
            role="user",
            original_value="/path/audio.wav",
            converted_value="/path/audio.wav",
            converted_value_data_type="audio_path",
            conversation_id=conversation_id,
        )
        with pytest.raises(
            ValueError,
            match="This target supports only the following data types.*If your target does support this, set the"
            " custom_configuration parameter accordingly",
        ):
            video_target._validate_request(message=Message([msg_text, msg_audio]))

    def test_validate_rejects_remix_with_image(self, video_target: OpenAIVideoTarget):
        """Test validation rejects remix mode combined with image input."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="remix prompt",
            converted_value="remix prompt",
            prompt_metadata={"video_id": "existing_video_123"},
            conversation_id=conversation_id,
        )
        msg_image = MessagePiece(
            role="user",
            original_value="/path/image.png",
            converted_value="/path/image.png",
            converted_value_data_type="image_path",
            conversation_id=conversation_id,
        )
        with pytest.raises(ValueError, match="Cannot use image input in remix mode"):
            video_target._validate_request(message=Message([msg_text, msg_image]))


@pytest.mark.usefixtures("patch_central_database")
class TestVideoTargetImageToVideo:
    """Tests for image-to-video functionality."""

    @pytest.fixture
    def video_target(self) -> OpenAIVideoTarget:
        return OpenAIVideoTarget(
            endpoint="https://api.openai.com/v1",
            api_key="test",
            model_name="sora-2",
        )

    @pytest.mark.asyncio
    async def test_image_to_video_calls_create_with_input_reference(self, video_target: OpenAIVideoTarget):
        """Test that image-to-video mode passes input_reference to create_and_poll."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="animate this image",
            converted_value="animate this image",
            conversation_id=conversation_id,
        )
        msg_image = MessagePiece(
            role="user",
            original_value="/path/image.png",
            converted_value="/path/image.png",
            converted_value_data_type="image_path",
            conversation_id=conversation_id,
        )

        mock_video = MagicMock()
        mock_video.id = "video_img2vid"
        mock_video.status = "completed"
        mock_video.error = None
        mock_video.remixed_from_video_id = None

        mock_video_response = MagicMock()
        mock_video_response.content = b"video data"

        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/output.mp4"
        mock_serializer.save_data = AsyncMock()

        mock_image_serializer = MagicMock()
        mock_image_serializer.read_data = AsyncMock(return_value=b"image bytes")

        with (
            patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create,
            patch.object(
                video_target._async_client.videos, "download_content", new_callable=AsyncMock
            ) as mock_download,
            patch("pyrit.prompt_target.openai.openai_video_target.data_serializer_factory") as mock_factory,
            patch("pyrit.prompt_target.openai.openai_video_target.DataTypeSerializer.get_mime_type") as mock_mime,
        ):
            # First call returns image serializer, second call returns video serializer
            mock_factory.side_effect = [mock_image_serializer, mock_serializer]
            mock_create.return_value = mock_video
            mock_download.return_value = mock_video_response
            mock_mime.return_value = "image/png"

            response = await video_target.send_prompt_async(message=Message([msg_text, msg_image]))

            # Verify create_and_poll was called with input_reference as tuple with MIME type
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            # input_reference should be (filename, bytes, content_type) tuple
            input_ref = call_kwargs["input_reference"]
            assert isinstance(input_ref, tuple)
            assert input_ref[0] == "image.png"  # filename
            assert input_ref[1] == b"image bytes"  # content
            assert input_ref[2] == "image/png"  # MIME type
            assert call_kwargs["prompt"] == "animate this image"

            # Verify response
            assert len(response) == 1
            assert response[0].message_pieces[0].converted_value_data_type == "video_path"


@pytest.mark.usefixtures("patch_central_database")
class TestVideoTargetRemix:
    """Tests for video remix functionality."""

    @pytest.fixture
    def video_target(self) -> OpenAIVideoTarget:
        return OpenAIVideoTarget(
            endpoint="https://api.openai.com/v1",
            api_key="test",
            model_name="sora-2",
            custom_configuration=_VIDEO_PATH_CONFIGURATION,
        )

    @pytest.mark.asyncio
    async def test_remix_calls_remix_and_poll(self, video_target: OpenAIVideoTarget):
        """Test that remix mode calls remix() and poll()."""
        msg = MessagePiece(
            role="user",
            original_value="make it more dramatic",
            converted_value="make it more dramatic",
            prompt_metadata={"video_id": "existing_video_123"},
            conversation_id=str(uuid.uuid4()),
        )

        mock_remix_video = MagicMock()
        mock_remix_video.id = "remixed_video_456"
        mock_remix_video.status = "in_progress"

        mock_polled_video = MagicMock()
        mock_polled_video.id = "remixed_video_456"
        mock_polled_video.status = "completed"
        mock_polled_video.error = None
        mock_polled_video.remixed_from_video_id = "existing_video_123"

        mock_video_response = MagicMock()
        mock_video_response.content = b"remixed video data"

        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/remixed.mp4"
        mock_serializer.save_data = AsyncMock()

        with (
            patch.object(video_target._async_client.videos, "remix", new_callable=AsyncMock) as mock_remix,
            patch.object(video_target._async_client.videos, "poll", new_callable=AsyncMock) as mock_poll,
            patch.object(
                video_target._async_client.videos, "download_content", new_callable=AsyncMock
            ) as mock_download,
            patch("pyrit.prompt_target.openai.openai_video_target.data_serializer_factory") as mock_factory,
        ):
            mock_remix.return_value = mock_remix_video
            mock_poll.return_value = mock_polled_video
            mock_download.return_value = mock_video_response
            mock_factory.return_value = mock_serializer

            response = await video_target.send_prompt_async(message=Message([msg]))

            # Verify remix was called with correct params
            mock_remix.assert_called_once_with("existing_video_123", prompt="make it more dramatic")
            # Verify poll was called (since status was in_progress)
            mock_poll.assert_called_once_with("remixed_video_456")

            # Verify response
            assert len(response) == 1
            assert response[0].message_pieces[0].converted_value_data_type == "video_path"

    @pytest.mark.asyncio
    async def test_remix_skips_poll_if_completed(self, video_target: OpenAIVideoTarget):
        """Test that remix mode skips poll() if already completed."""
        msg = MessagePiece(
            role="user",
            original_value="remix prompt",
            converted_value="remix prompt",
            prompt_metadata={"video_id": "existing_video_123"},
            conversation_id=str(uuid.uuid4()),
        )

        mock_video = MagicMock()
        mock_video.id = "remixed_video"
        mock_video.status = "completed"
        mock_video.error = None
        mock_video.remixed_from_video_id = "existing_video_123"

        mock_video_response = MagicMock()
        mock_video_response.content = b"remixed video data"

        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/remixed.mp4"
        mock_serializer.save_data = AsyncMock()

        with (
            patch.object(video_target._async_client.videos, "remix", new_callable=AsyncMock) as mock_remix,
            patch.object(video_target._async_client.videos, "poll", new_callable=AsyncMock) as mock_poll,
            patch.object(
                video_target._async_client.videos, "download_content", new_callable=AsyncMock
            ) as mock_download,
            patch("pyrit.prompt_target.openai.openai_video_target.data_serializer_factory") as mock_factory,
        ):
            mock_remix.return_value = mock_video
            mock_download.return_value = mock_video_response
            mock_factory.return_value = mock_serializer

            await video_target.send_prompt_async(message=Message([msg]))

            # Verify poll was NOT called since status was already completed
            mock_poll.assert_not_called()

    @pytest.mark.asyncio
    async def test_remix_with_text_and_video_path_pieces(self, video_target: OpenAIVideoTarget):
        """Test full send_prompt_async with text + video_path pieces (UI remix flow).

        When the frontend sends a remix request, it includes both a text piece
        and a video_path piece, both carrying matching video_id in prompt_metadata.
        The target should extract video_id from the text piece and call remix().
        """
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="make it more dramatic",
            converted_value="make it more dramatic",
            prompt_metadata={"video_id": "vid_from_ui_123"},
            conversation_id=conversation_id,
        )
        msg_video = MessagePiece(
            role="user",
            original_value="/path/to/original.mp4",
            converted_value="/path/to/original.mp4",
            converted_value_data_type="video_path",
            prompt_metadata={"video_id": "vid_from_ui_123"},
            conversation_id=conversation_id,
        )

        mock_remix_video = MagicMock()
        mock_remix_video.id = "remixed_output_456"
        mock_remix_video.status = "completed"
        mock_remix_video.error = None
        mock_remix_video.remixed_from_video_id = "vid_from_ui_123"

        mock_video_response = MagicMock()
        mock_video_response.content = b"remixed video data"

        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/remixed.mp4"
        mock_serializer.save_data = AsyncMock()

        with (
            patch.object(video_target._async_client.videos, "remix", new_callable=AsyncMock) as mock_remix,
            patch.object(video_target._async_client.videos, "poll", new_callable=AsyncMock),
            patch.object(
                video_target._async_client.videos, "download_content", new_callable=AsyncMock
            ) as mock_download,
            patch("pyrit.prompt_target.openai.openai_video_target.data_serializer_factory") as mock_factory,
        ):
            mock_remix.return_value = mock_remix_video
            mock_download.return_value = mock_video_response
            mock_factory.return_value = mock_serializer

            response = await video_target.send_prompt_async(message=Message([msg_text, msg_video]))

            # Verify remix was called with the video_id from text metadata
            mock_remix.assert_called_once_with("vid_from_ui_123", prompt="make it more dramatic")

            # Verify response
            assert len(response) == 1
            assert response[0].message_pieces[0].converted_value_data_type == "video_path"


@pytest.mark.usefixtures("patch_central_database")
class TestVideoTargetMetadata:
    """Tests for video_id metadata storage in responses."""

    @pytest.fixture
    def video_target(self) -> OpenAIVideoTarget:
        return OpenAIVideoTarget(
            endpoint="https://api.openai.com/v1",
            api_key="test",
            model_name="sora-2",
        )

    @pytest.mark.asyncio
    async def test_response_includes_video_id_metadata(self, video_target: OpenAIVideoTarget):
        """Test that response includes video_id in prompt_metadata for chaining."""
        msg = MessagePiece(
            role="user",
            original_value="test prompt",
            converted_value="test prompt",
            conversation_id=str(uuid.uuid4()),
        )

        mock_video = MagicMock()
        mock_video.id = "new_video_789"
        mock_video.status = "completed"
        mock_video.error = None
        mock_video.remixed_from_video_id = None

        mock_video_response = MagicMock()
        mock_video_response.content = b"video data"

        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/video.mp4"
        mock_serializer.save_data = AsyncMock()

        with (
            patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create,
            patch.object(
                video_target._async_client.videos, "download_content", new_callable=AsyncMock
            ) as mock_download,
            patch("pyrit.prompt_target.openai.openai_video_target.data_serializer_factory") as mock_factory,
        ):
            mock_create.return_value = mock_video
            mock_download.return_value = mock_video_response
            mock_factory.return_value = mock_serializer

            response = await video_target.send_prompt_async(message=Message([msg]))

            # Verify response contains video_id in metadata for chaining
            response_piece = response[0].message_pieces[0]
            assert response_piece.prompt_metadata is not None
            assert response_piece.prompt_metadata.get("video_id") == "new_video_789"


@pytest.mark.usefixtures("patch_central_database")
class TestVideoTargetEdgeCases:
    """Tests for edge cases and error scenarios."""

    @pytest.fixture
    def video_target(self) -> OpenAIVideoTarget:
        return OpenAIVideoTarget(
            endpoint="https://api.openai.com/v1",
            api_key="test",
            model_name="sora-2",
        )

    def test_validate_rejects_empty_message(self, video_target: OpenAIVideoTarget):
        """Test that empty messages are rejected (by Message constructor)."""
        with pytest.raises(ValueError, match="at least one message piece"):
            Message([])

    def test_validate_rejects_no_text_piece(self, video_target: OpenAIVideoTarget):
        """Test validation rejects message without text piece."""
        msg = MessagePiece(
            role="user",
            original_value="/path/image.png",
            converted_value="/path/image.png",
            converted_value_data_type="image_path",
        )
        with pytest.raises(ValueError, match="Expected exactly 1 text piece"):
            video_target._validate_request(message=Message([msg]))

    @pytest.mark.asyncio
    async def test_image_to_video_with_jpeg(self, video_target: OpenAIVideoTarget):
        """Test image-to-video with JPEG image format."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="animate",
            converted_value="animate",
            conversation_id=conversation_id,
        )
        msg_image = MessagePiece(
            role="user",
            original_value="/path/image.jpg",
            converted_value="/path/image.jpg",
            converted_value_data_type="image_path",
            conversation_id=conversation_id,
        )

        mock_video = MagicMock()
        mock_video.id = "video_jpeg"
        mock_video.status = "completed"
        mock_video.error = None
        mock_video.remixed_from_video_id = None

        mock_video_response = MagicMock()
        mock_video_response.content = b"video data"

        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/output.mp4"
        mock_serializer.save_data = AsyncMock()

        mock_image_serializer = MagicMock()
        mock_image_serializer.read_data = AsyncMock(return_value=b"jpeg bytes")

        with (
            patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create,
            patch.object(
                video_target._async_client.videos, "download_content", new_callable=AsyncMock
            ) as mock_download,
            patch("pyrit.prompt_target.openai.openai_video_target.data_serializer_factory") as mock_factory,
            patch("pyrit.prompt_target.openai.openai_video_target.DataTypeSerializer.get_mime_type") as mock_mime,
        ):
            mock_factory.side_effect = [mock_image_serializer, mock_serializer]
            mock_create.return_value = mock_video
            mock_download.return_value = mock_video_response
            mock_mime.return_value = "image/jpeg"

            response = await video_target.send_prompt_async(message=Message([msg_text, msg_image]))

            # Verify JPEG MIME type is used
            call_kwargs = mock_create.call_args.kwargs
            input_ref = call_kwargs["input_reference"]
            assert input_ref[2] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_image_to_video_with_webp_uses_guess_type_fallback(self, video_target: OpenAIVideoTarget):
        """Test image-to-video correctly identifies .webp via guess_type fallback."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="animate",
            converted_value="animate",
            conversation_id=conversation_id,
        )
        msg_image = MessagePiece(
            role="user",
            original_value="/path/image.webp",
            converted_value="/path/image.webp",
            converted_value_data_type="image_path",
            conversation_id=conversation_id,
        )

        mock_video = MagicMock()
        mock_video.id = "video_webp"
        mock_video.status = "completed"
        mock_video.error = None
        mock_video.remixed_from_video_id = None

        mock_video_response = MagicMock()
        mock_video_response.content = b"video data"

        mock_serializer = MagicMock()
        mock_serializer.value = "/path/to/output.mp4"
        mock_serializer.save_data = AsyncMock()

        mock_image_serializer = MagicMock()
        mock_image_serializer.read_data = AsyncMock(return_value=b"webp bytes")

        with (
            patch.object(video_target._async_client.videos, "create_and_poll", new_callable=AsyncMock) as mock_create,
            patch.object(
                video_target._async_client.videos, "download_content", new_callable=AsyncMock
            ) as mock_download,
            patch("pyrit.prompt_target.openai.openai_video_target.data_serializer_factory") as mock_factory,
            patch("pyrit.prompt_target.openai.openai_video_target.DataTypeSerializer.get_mime_type") as mock_mime,
            patch(
                "pyrit.prompt_target.openai.openai_video_target.guess_type",
                return_value=("image/webp", None),
            ),
        ):
            mock_factory.side_effect = [mock_image_serializer, mock_serializer]
            mock_create.return_value = mock_video
            mock_download.return_value = mock_video_response
            mock_mime.return_value = None  # strict=True returns None for .webp

            response = await video_target.send_prompt_async(message=Message([msg_text, msg_image]))

            # Verify webp MIME type is correctly resolved via guess_type fallback
            call_kwargs = mock_create.call_args.kwargs
            input_ref = call_kwargs["input_reference"]
            assert input_ref[2] == "image/webp"

    @pytest.mark.asyncio
    async def test_image_to_video_with_unknown_mime_raises_error(self, video_target: OpenAIVideoTarget):
        """Test image-to-video raises ValueError when image format is unsupported."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="animate",
            converted_value="animate",
            conversation_id=conversation_id,
        )
        msg_image = MessagePiece(
            role="user",
            original_value="/path/image.unknown",
            converted_value="/path/image.unknown",
            converted_value_data_type="image_path",
            conversation_id=conversation_id,
        )

        mock_image_serializer = MagicMock()
        mock_image_serializer.read_data = AsyncMock(return_value=b"unknown bytes")

        with (
            patch("pyrit.prompt_target.openai.openai_video_target.data_serializer_factory") as mock_factory,
            patch("pyrit.prompt_target.openai.openai_video_target.DataTypeSerializer.get_mime_type") as mock_mime,
            pytest.raises(ValueError, match="Unsupported image format"),
        ):
            mock_factory.return_value = mock_image_serializer
            mock_mime.return_value = None  # MIME type cannot be determined

            await video_target.send_prompt_async(message=Message([msg_text, msg_image]))

    @pytest.mark.asyncio
    async def test_remix_with_failed_status(self, video_target: OpenAIVideoTarget):
        """Test remix mode handles failed video generation."""
        msg = MessagePiece(
            role="user",
            original_value="remix this",
            converted_value="remix this",
            prompt_metadata={"video_id": "existing_video"},
            conversation_id=str(uuid.uuid4()),
        )

        mock_video = MagicMock()
        mock_video.id = "failed_remix"
        mock_video.status = "failed"
        mock_error = MagicMock()
        mock_error.code = "internal_error"
        mock_video.error = mock_error

        with (
            patch.object(video_target._async_client.videos, "remix", new_callable=AsyncMock) as mock_remix,
            patch.object(video_target._async_client.videos, "poll", new_callable=AsyncMock) as mock_poll,
        ):
            mock_remix.return_value = mock_video
            # Don't need poll since status is already "failed"

            response = await video_target.send_prompt_async(message=Message([msg]))

            # Verify response is processing error
            response_piece = response[0].message_pieces[0]
            assert response_piece.response_error == "processing"

    def test_supported_resolutions(self, video_target: OpenAIVideoTarget):
        """Test that all supported resolutions are valid."""
        for resolution in OpenAIVideoTarget.SUPPORTED_RESOLUTIONS:
            target = OpenAIVideoTarget(
                endpoint="https://api.openai.com/v1",
                api_key="test",
                model_name="sora-2",
                resolution_dimensions=resolution,
            )
            assert target._size == resolution

    def test_supported_durations(self, video_target: OpenAIVideoTarget):
        """Test that all supported durations are valid."""
        for duration in OpenAIVideoTarget.SUPPORTED_DURATIONS:
            target = OpenAIVideoTarget(
                endpoint="https://api.openai.com/v1",
                api_key="test",
                model_name="sora-2",
                n_seconds=duration,
            )
            assert target._n_seconds == duration


def test_video_validate_previous_conversations(
    video_target: OpenAIVideoTarget, sample_conversations: MutableSequence[MessagePiece]
):
    message_piece = sample_conversations[0]

    mock_memory = MagicMock()
    mock_memory.get_message_pieces.return_value = sample_conversations
    mock_memory.add_message_to_memory = AsyncMock()

    video_target._memory = mock_memory

    request = Message(message_pieces=[message_piece])

    with pytest.raises(
        ValueError,
        match="This target only supports a single turn conversation.*If your target does support this, set the"
        " custom_configuration parameter accordingly",
    ):
        video_target._validate_request(message=request)


@pytest.mark.usefixtures("patch_central_database")
class TestVideoTargetRemixValidation:
    """Tests for _validate_video_remix_pieces and video_path validation."""

    @pytest.fixture
    def video_target(self) -> OpenAIVideoTarget:
        return OpenAIVideoTarget(
            endpoint="https://api.openai.com/v1",
            api_key="test",
            model_name="sora-2",
            custom_configuration=_VIDEO_PATH_CONFIGURATION,
        )

    def test_validate_accepts_text_and_video_path(self, video_target: OpenAIVideoTarget) -> None:
        """Test validation accepts text + video_path pieces."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="remix this",
            converted_value="remix this",
            conversation_id=conversation_id,
        )
        msg_video = MessagePiece(
            role="user",
            original_value="/path/video.mp4",
            converted_value="/path/video.mp4",
            converted_value_data_type="video_path",
            conversation_id=conversation_id,
        )
        # Should not raise
        video_target._validate_request(message=Message([msg_text, msg_video]))

    def test_validate_rejects_video_path_and_image_path(self, video_target: OpenAIVideoTarget) -> None:
        """Test validation rejects combining video_path and image_path."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="remix",
            converted_value="remix",
            conversation_id=conversation_id,
        )
        msg_video = MessagePiece(
            role="user",
            original_value="/path/video.mp4",
            converted_value="/path/video.mp4",
            converted_value_data_type="video_path",
            conversation_id=conversation_id,
        )
        msg_image = MessagePiece(
            role="user",
            original_value="/path/image.png",
            converted_value="/path/image.png",
            converted_value_data_type="image_path",
            conversation_id=conversation_id,
        )
        with pytest.raises(ValueError, match="Cannot combine video_path and image_path"):
            video_target._validate_request(message=Message([msg_text, msg_video, msg_image]))

    def test_remix_keeps_video_path_pieces_when_ids_match(self, video_target: OpenAIVideoTarget) -> None:
        """Test that video_path pieces are preserved after validation so normalizer stores them."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="remix",
            converted_value="remix",
            prompt_metadata={"video_id": "vid_123"},
            conversation_id=conversation_id,
        )
        msg_video = MessagePiece(
            role="user",
            original_value="/path/video.mp4",
            converted_value="/path/video.mp4",
            converted_value_data_type="video_path",
            prompt_metadata={"video_id": "vid_123"},
            conversation_id=conversation_id,
        )
        message = Message([msg_text, msg_video])

        OpenAIVideoTarget._validate_video_remix_pieces(message=message)

        assert msg_text.prompt_metadata["video_id"] == "vid_123"
        assert len(message.message_pieces) == 2
        assert any(p.converted_value_data_type == "video_path" for p in message.message_pieces)

    def test_remix_raises_when_video_ids_mismatch(self, video_target: OpenAIVideoTarget) -> None:
        """Test that mismatched video_id values between text and video_path raise ValueError."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="remix",
            converted_value="remix",
            prompt_metadata={"video_id": "vid_123"},
            conversation_id=conversation_id,
        )
        msg_video = MessagePiece(
            role="user",
            original_value="/path/video.mp4",
            converted_value="/path/video.mp4",
            converted_value_data_type="video_path",
            prompt_metadata={"video_id": "vid_DIFFERENT"},
            conversation_id=conversation_id,
        )
        message = Message([msg_text, msg_video])

        with pytest.raises(ValueError, match="video_id mismatch"):
            OpenAIVideoTarget._validate_video_remix_pieces(message=message)

    def test_remix_raises_when_text_missing_video_id(self, video_target: OpenAIVideoTarget) -> None:
        """Test that video_path without video_id on text piece raises ValueError."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="remix",
            converted_value="remix",
            conversation_id=conversation_id,
        )
        msg_video = MessagePiece(
            role="user",
            original_value="/path/video.mp4",
            converted_value="/path/video.mp4",
            converted_value_data_type="video_path",
            conversation_id=conversation_id,
        )
        message = Message([msg_text, msg_video])

        with pytest.raises(ValueError, match="missing.*video_id"):
            OpenAIVideoTarget._validate_video_remix_pieces(message=message)

    def test_remix_no_op_without_video_path(self, video_target: OpenAIVideoTarget) -> None:
        """Test that _validate_video_remix_pieces is a no-op for text-only messages."""
        msg_text = MessagePiece(
            role="user",
            original_value="generate a cat video",
            converted_value="generate a cat video",
        )
        message = Message([msg_text])

        OpenAIVideoTarget._validate_video_remix_pieces(message=message)

        assert "video_id" not in (msg_text.prompt_metadata or {})

    def test_remix_raises_when_video_path_missing_video_id(self, video_target: OpenAIVideoTarget) -> None:
        """Test that video_path piece without video_id raises ValueError."""
        conversation_id = str(uuid.uuid4())
        msg_text = MessagePiece(
            role="user",
            original_value="remix",
            converted_value="remix",
            prompt_metadata={"video_id": "vid_123"},
            conversation_id=conversation_id,
        )
        msg_video = MessagePiece(
            role="user",
            original_value="/path/video.mp4",
            converted_value="/path/video.mp4",
            converted_value_data_type="video_path",
            conversation_id=conversation_id,
        )
        message = Message([msg_text, msg_video])

        with pytest.raises(ValueError, match="video_path piece is missing.*video_id"):
            OpenAIVideoTarget._validate_video_remix_pieces(message=message)
