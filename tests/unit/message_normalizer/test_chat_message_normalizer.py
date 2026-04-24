# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.message_normalizer import ChatMessageNormalizer
from pyrit.models import ChatMessage, Message, MessagePiece
from pyrit.models.literals import ChatMessageRole, PromptDataType


def _make_message(role: ChatMessageRole, content: str) -> Message:
    """Helper to create a Message from role and content."""
    return Message(message_pieces=[MessagePiece(role=role, original_value=content)])


def _make_multipart_message(role: ChatMessageRole, pieces_data: list[tuple[str, PromptDataType]]) -> Message:
    """Helper to create a multipart Message.

    Args:
        role: The role for all pieces.
        pieces_data: List of (content, data_type) tuples.
    """
    pieces = [
        MessagePiece(
            role=role,
            original_value=content,
            original_value_data_type=data_type,
            converted_value_data_type=data_type,
        )
        for content, data_type in pieces_data
    ]
    return Message(message_pieces=pieces)


class TestChatMessageNormalizerNormalizeAsync:
    """Tests for ChatMessageNormalizer.normalize_async."""

    @pytest.mark.asyncio
    async def test_empty_list_raises(self):
        """Test that empty message list raises ValueError."""
        normalizer = ChatMessageNormalizer()
        with pytest.raises(ValueError, match="Messages list cannot be empty"):
            await normalizer.normalize_async(messages=[])

    @pytest.mark.asyncio
    async def test_single_text_message(self):
        """Test normalizing a single text message."""
        normalizer = ChatMessageNormalizer()
        messages = [_make_message("user", "Hello world")]

        result = await normalizer.normalize_async(messages)

        assert len(result) == 1
        assert isinstance(result[0], ChatMessage)
        assert result[0].role == "user"
        assert result[0].content == "Hello world"

    @pytest.mark.asyncio
    async def test_multiple_messages(self):
        """Test normalizing multiple messages."""
        normalizer = ChatMessageNormalizer()
        messages = [
            _make_message("system", "You are helpful"),
            _make_message("user", "Hello"),
            _make_message("assistant", "Hi there!"),
        ]

        result = await normalizer.normalize_async(messages)

        assert len(result) == 3
        assert result[0].role == "system"
        assert result[0].content == "You are helpful"
        assert result[1].role == "user"
        assert result[1].content == "Hello"
        assert result[2].role == "assistant"
        assert result[2].content == "Hi there!"

    @pytest.mark.asyncio
    async def test_developer_role_translation(self):
        """Test that system role is translated to developer when configured."""
        normalizer = ChatMessageNormalizer(use_developer_role=True)
        messages = [
            _make_message("system", "You are helpful"),
            _make_message("user", "Hello"),
        ]

        result = await normalizer.normalize_async(messages)

        assert result[0].role == "developer"
        assert result[1].role == "user"

    @pytest.mark.asyncio
    async def test_system_message_behavior_squash(self):
        """Test that system_message_behavior='squash' merges system into user message."""
        normalizer = ChatMessageNormalizer(system_message_behavior="squash")
        messages = [
            _make_message("system", "You are helpful"),
            _make_message("user", "Hello"),
        ]

        result = await normalizer.normalize_async(messages)

        # System message should be squashed into first user message
        assert len(result) == 1
        assert result[0].role == "user"
        assert "You are helpful" in result[0].content
        assert "Hello" in result[0].content

    @pytest.mark.asyncio
    async def test_multipart_text_message(self):
        """Test that multipart text messages have list content."""
        normalizer = ChatMessageNormalizer()
        conversation_id = "test-conv-id"
        message = Message(
            message_pieces=[
                MessagePiece(role="user", original_value="Part 1", conversation_id=conversation_id),
                MessagePiece(role="user", original_value="Part 2", conversation_id=conversation_id),
            ]
        )

        result = await normalizer.normalize_async([message])

        assert len(result) == 1
        assert isinstance(result[0].content, list)
        assert len(result[0].content) == 2
        assert result[0].content[0] == {"type": "text", "text": "Part 1"}
        assert result[0].content[1] == {"type": "text", "text": "Part 2"}

    @pytest.mark.asyncio
    async def test_image_path_conversion(self):
        """Test that image_path is converted to base64 data URL."""
        normalizer = ChatMessageNormalizer()

        with patch(
            "pyrit.message_normalizer.chat_message_normalizer.convert_local_image_to_data_url",
            new_callable=AsyncMock,
        ) as mock_convert:
            mock_convert.return_value = "data:image/png;base64,abc123"

            message = Message(
                message_pieces=[
                    MessagePiece(
                        role="user",
                        original_value="/path/to/image.png",
                        original_value_data_type="image_path",
                        converted_value_data_type="image_path",
                    )
                ]
            )

            result = await normalizer.normalize_async([message])

            assert isinstance(result[0].content, list)
            assert result[0].content[0]["type"] == "image_url"
            assert result[0].content[0]["image_url"]["url"] == "data:image/png;base64,abc123"

    @pytest.mark.asyncio
    async def test_url_conversion(self):
        """Test that URL data type is converted to image_url format."""
        normalizer = ChatMessageNormalizer()
        message = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="https://example.com/image.png",
                    original_value_data_type="url",
                    converted_value_data_type="url",
                )
            ]
        )

        result = await normalizer.normalize_async([message])

        assert isinstance(result[0].content, list)
        assert result[0].content[0]["type"] == "image_url"
        assert result[0].content[0]["image_url"]["url"] == "https://example.com/image.png"

    @pytest.mark.asyncio
    async def test_unsupported_data_type_raises(self):
        """Test that unsupported data type raises ValueError at MessagePiece creation."""
        with pytest.raises(ValueError, match="is not a valid data type"):
            MessagePiece(
                role="user",
                original_value="some data",
                original_value_data_type="unsupported_type",
                converted_value_data_type="unsupported_type",
            )


class TestChatMessageNormalizerAudioConversion:
    """Tests for ChatMessageNormalizer audio conversion."""

    @pytest.mark.asyncio
    async def test_audio_file_not_found_raises(self):
        """Test that non-existent audio file raises FileNotFoundError."""
        normalizer = ChatMessageNormalizer()
        message = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="/nonexistent/audio.wav",
                    original_value_data_type="audio_path",
                    converted_value_data_type="audio_path",
                )
            ]
        )

        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            await normalizer.normalize_async([message])

    @pytest.mark.asyncio
    async def test_unsupported_audio_format_raises(self):
        """Test that unsupported audio format raises ValueError."""
        normalizer = ChatMessageNormalizer()
        message = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="/path/to/audio.ogg",
                    original_value_data_type="audio_path",
                    converted_value_data_type="audio_path",
                )
            ]
        )

        with pytest.raises(ValueError, match="Unsupported audio format"):
            await normalizer.normalize_async([message])

    @pytest.mark.asyncio
    async def test_wav_audio_conversion(self):
        """Test that WAV audio file is converted to input_audio format."""
        normalizer = ChatMessageNormalizer()

        # Create a temporary WAV file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake wav data")
            temp_path = f.name

        try:
            message = Message(
                message_pieces=[
                    MessagePiece(
                        role="user",
                        original_value=temp_path,
                        original_value_data_type="audio_path",
                        converted_value_data_type="audio_path",
                    )
                ]
            )

            result = await normalizer.normalize_async([message])

            assert isinstance(result[0].content, list)
            assert result[0].content[0]["type"] == "input_audio"
            assert result[0].content[0]["input_audio"]["format"] == "wav"
            assert result[0].content[0]["input_audio"]["data"] == base64.b64encode(b"fake wav data").decode("utf-8")
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    async def test_mp3_audio_conversion(self):
        """Test that MP3 audio file is converted to input_audio format."""
        normalizer = ChatMessageNormalizer()

        # Create a temporary MP3 file
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"fake mp3 data")
            temp_path = f.name

        try:
            message = Message(
                message_pieces=[
                    MessagePiece(
                        role="user",
                        original_value=temp_path,
                        original_value_data_type="audio_path",
                        converted_value_data_type="audio_path",
                    )
                ]
            )

            result = await normalizer.normalize_async([message])

            assert result[0].content[0]["input_audio"]["format"] == "mp3"
        finally:
            os.unlink(temp_path)


class TestChatMessageNormalizerNormalizeStringAsync:
    """Tests for ChatMessageNormalizer.normalize_string_async."""

    @pytest.mark.asyncio
    async def test_returns_json_string(self):
        """Test that normalize_string_async returns valid JSON."""
        normalizer = ChatMessageNormalizer()
        messages = [
            _make_message("user", "Hello"),
            _make_message("assistant", "Hi!"),
        ]

        result = await normalizer.normalize_string_async(messages)

        # Should be valid JSON
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["role"] == "user"
        assert parsed[0]["content"] == "Hello"
        assert parsed[1]["role"] == "assistant"
        assert parsed[1]["content"] == "Hi!"


class TestChatMessageNormalizerToDictsAsync:
    """Tests for ChatMessageNormalizer.normalize_to_dicts_async (inherited)."""

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        """Test that normalize_to_dicts_async returns list of dicts."""
        normalizer = ChatMessageNormalizer()
        messages = [
            _make_message("user", "Hello"),
            _make_message("assistant", "Hi!"),
        ]

        result = await normalizer.normalize_to_dicts_async(messages)

        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], dict)
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"
