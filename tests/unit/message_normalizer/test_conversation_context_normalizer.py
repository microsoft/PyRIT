# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.message_normalizer import ConversationContextNormalizer
from pyrit.models import Message, MessagePiece
from pyrit.models.literals import ChatMessageRole, PromptDataType


def _make_message(role: ChatMessageRole, content: str) -> Message:
    """Helper to create a Message from role and content."""
    return Message(message_pieces=[MessagePiece(role=role, original_value=content)])


def _make_message_with_converted(role: ChatMessageRole, original: str, converted: str) -> Message:
    """Helper to create a Message with different original and converted values."""
    return Message(message_pieces=[MessagePiece(role=role, original_value=original, converted_value=converted)])


def _make_non_text_message(
    role: ChatMessageRole, value: str, data_type: PromptDataType, context_description: str | None = None
) -> Message:
    """Helper to create a non-text Message."""
    metadata: dict[str, str | int] | None = (
        {"context_description": context_description} if context_description else None
    )
    return Message(
        message_pieces=[
            MessagePiece(
                role=role,
                original_value=value,
                original_value_data_type=data_type,
                converted_value_data_type=data_type,
                prompt_metadata=metadata,
            )
        ]
    )


class TestConversationContextNormalizerNormalizeStringAsync:
    """Tests for ConversationContextNormalizer.normalize_string_async."""

    @pytest.mark.asyncio
    async def test_empty_list_raises(self):
        """Test that empty message list raises ValueError."""
        normalizer = ConversationContextNormalizer()
        with pytest.raises(ValueError, match="Messages list cannot be empty"):
            await normalizer.normalize_string_async(messages=[])

    @pytest.mark.asyncio
    async def test_basic_conversation(self):
        """Test basic user-assistant conversation formatting."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_message("user", "Hello"),
            _make_message("assistant", "Hi there!"),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert "Turn 1:" in result
        assert "User: Hello" in result
        assert "Assistant: Hi there!" in result

    @pytest.mark.asyncio
    async def test_skips_system_messages(self):
        """Test that system messages are skipped in output."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_message("system", "You are a helpful assistant"),
            _make_message("user", "Hello"),
            _make_message("assistant", "Hi!"),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert "system" not in result.lower()
        assert "You are a helpful assistant" not in result
        assert "User: Hello" in result
        assert "Assistant: Hi!" in result

    @pytest.mark.asyncio
    async def test_turn_numbering(self):
        """Test that turns are numbered correctly."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_message("user", "First question"),
            _make_message("assistant", "First answer"),
            _make_message("user", "Second question"),
            _make_message("assistant", "Second answer"),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert "Turn 1:" in result
        assert "Turn 2:" in result

    @pytest.mark.asyncio
    async def test_shows_original_if_different_from_converted(self):
        """Test that original value is shown when different from converted."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_message_with_converted("user", "original text", "converted text"),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert "converted text" in result
        assert "(original: original text)" in result
