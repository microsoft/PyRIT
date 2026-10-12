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


def _make_multipart_message(role: ChatMessageRole, pieces_data: list[tuple[str, PromptDataType]]) -> Message:
    """Helper to create a multipart Message from (content, data_type) tuples."""
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


class TestConversationContextNormalizerNormalizeStringAsync:
    """Tests for ConversationContextNormalizer.normalize_string_async."""

    async def test_empty_list_raises(self):
        """Test that empty message list raises ValueError."""
        normalizer = ConversationContextNormalizer()
        with pytest.raises(ValueError, match="Messages list cannot be empty"):
            await normalizer.normalize_string_async(messages=[])

    async def test_basic_conversation(self):
        """Test basic user-assistant conversation formatting."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_message("user", "Hello"),
            _make_message("assistant", "Hi there!"),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert "Turn 1:" in result
        assert "user: Hello" in result
        assert "assistant: Hi there!" in result

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
        assert "user: Hello" in result
        assert "assistant: Hi!" in result

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

    async def test_shows_original_if_different_from_converted(self):
        """Test that original value is shown when different from converted."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_message_with_converted("user", "original text", "converted text"),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert "converted text" in result
        assert "(original: original text)" in result

    async def test_preserves_tool_role_label(self):
        """Test that tool messages keep the Tool label in context output."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_message("user", "Call the weather tool"),
            _make_message("tool", "72F and sunny"),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert "tool: 72F and sunny" in result
        assert "assistant: 72F and sunny" not in result

    async def test_preserves_developer_role_label(self):
        """Test that developer messages keep the Developer label in context output."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_message("user", "Use concise units"),
            _make_message("developer", "Prefer metric units"),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert "developer: Prefer metric units" in result
        assert "assistant: Prefer metric units" not in result

    async def test_multipart_user_message_is_one_turn(self):
        """Test that a user message with several pieces opens a single turn."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_multipart_message("user", [("Describe this picture", "text"), ("bench.png", "image_path")]),
            _make_message("assistant", "It shows a lab bench."),
            _make_message("user", "What is on the bench?"),
            _make_message("assistant", "Glassware."),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert result == (
            "Turn 1:\n"
            "user: Describe this picture\n"
            "user: [Image_path]\n"
            "assistant: It shows a lab bench.\n"
            "Turn 2:\n"
            "user: What is on the bench?\n"
            "assistant: Glassware."
        )

    async def test_multipart_system_message_is_skipped(self):
        """Test that every piece of a system message is skipped without opening a turn."""
        normalizer = ConversationContextNormalizer()
        messages = [
            _make_multipart_message("system", [("Rule one", "text"), ("Rule two", "text")]),
            _make_message("user", "Hello"),
            _make_message("assistant", "Hi!"),
        ]

        result = await normalizer.normalize_string_async(messages)

        assert result == "Turn 1:\nuser: Hello\nassistant: Hi!"
