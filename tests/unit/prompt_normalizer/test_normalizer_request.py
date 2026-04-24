# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import uuid

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_normalizer import NormalizerRequest


def test_normalizer_request_validates_sequence():
    # Test that NormalizerRequest accepts messages with consistent sequences
    message = Message.from_prompt(prompt="Hello", role="user")
    request = NormalizerRequest(
        message=message,
        conversation_id=str(uuid.uuid4()),
    )

    # Verify request was created successfully
    assert request.message == message
    assert len(request.message.message_pieces) == 1


def test_normalizer_request_validates_empty_message():
    # Test that Message constructor itself validates non-empty message_pieces
    with pytest.raises(ValueError) as exc_info:
        Message(message_pieces=[])

    assert "Message must have at least one message piece" in str(exc_info.value)


class TestNormalizerRequestWithMessage:
    """Tests for NormalizerRequest with the new Message parameter."""

    def test_normalizer_request_accepts_simple_message(self):
        """Test that NormalizerRequest accepts a simple single-piece message."""
        message = Message.from_prompt(prompt="Test prompt", role="user")
        request = NormalizerRequest(
            message=message,
            conversation_id=str(uuid.uuid4()),
        )

        assert request.message == message
        assert request.message.message_pieces[0].original_value == "Test prompt"

    def test_normalizer_request_accepts_multi_piece_message(self):
        """Test that NormalizerRequest accepts multi-piece messages."""
        conv_id = str(uuid.uuid4())
        piece1 = MessagePiece(
            role="user",
            original_value="Text content",
            conversation_id=conv_id,
            sequence=1,
        )
        piece2 = MessagePiece(
            role="user",
            original_value="Image content",
            original_value_data_type="image_path",
            conversation_id=conv_id,
            sequence=1,
        )
        message = Message(message_pieces=[piece1, piece2])

        request = NormalizerRequest(
            message=message,
            conversation_id=conv_id,
        )

        assert len(request.message.message_pieces) == 2
        assert request.message.message_pieces[0].original_value == "Text content"
        assert request.message.message_pieces[1].original_value == "Image content"

    def test_normalizer_request_with_converter_configurations(self):
        """Test that NormalizerRequest stores converter configurations."""
        from pyrit.prompt_converter import Base64Converter
        from pyrit.prompt_normalizer import PromptConverterConfiguration

        message = Message.from_prompt(prompt="Test", role="user")
        request_converters = [PromptConverterConfiguration(converters=[Base64Converter()])]
        response_converters = [PromptConverterConfiguration(converters=[Base64Converter()])]

        request = NormalizerRequest(
            message=message,
            request_converter_configurations=request_converters,
            response_converter_configurations=response_converters,
            conversation_id=str(uuid.uuid4()),
        )

        assert len(request.request_converter_configurations) == 1
        assert len(request.response_converter_configurations) == 1

    def test_normalizer_request_without_conversation_id(self):
        """Test that NormalizerRequest works without explicit conversation_id."""
        message = Message.from_prompt(prompt="Test", role="user")
        request = NormalizerRequest(message=message)

        assert request.conversation_id is None
        assert request.message == message

    def test_normalizer_request_default_converter_configurations(self):
        """Test that NormalizerRequest has empty converter configurations by default."""
        message = Message.from_prompt(prompt="Test", role="user")
        request = NormalizerRequest(message=message)

        assert request.request_converter_configurations == []
        assert request.response_converter_configurations == []


class TestMessageValidationInNormalizerRequest:
    """Tests ensuring Message validation catches issues before NormalizerRequest."""

    def test_message_rejects_different_sequences(self):
        """Test that Message constructor rejects pieces with different sequences."""
        conv_id = str(uuid.uuid4())
        piece1 = MessagePiece(role="user", original_value="test1", sequence=1, conversation_id=conv_id)
        piece2 = MessagePiece(role="user", original_value="test2", sequence=2, conversation_id=conv_id)

        with pytest.raises(ValueError, match="Inconsistent sequences within the same message entry"):
            Message(message_pieces=[piece1, piece2])

    def test_message_rejects_different_conversation_ids(self):
        """Test that Message constructor rejects pieces with different conversation IDs."""
        piece1 = MessagePiece(role="user", original_value="test1", conversation_id="conv1")
        piece2 = MessagePiece(role="user", original_value="test2", conversation_id="conv2")

        with pytest.raises(ValueError, match="Conversation ID mismatch"):
            Message(message_pieces=[piece1, piece2])

    def test_message_rejects_different_roles(self):
        """Test that Message constructor rejects pieces with different roles."""
        conv_id = str(uuid.uuid4())
        piece1 = MessagePiece(role="user", original_value="test1", conversation_id=conv_id)
        piece2 = MessagePiece(role="assistant", original_value="test2", conversation_id=conv_id)

        with pytest.raises(ValueError, match="Inconsistent roles within the same message entry"):
            Message(message_pieces=[piece1, piece2])
