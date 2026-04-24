# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.common.path import HOME_PATH
from pyrit.message_normalizer import TokenizerTemplateNormalizer
from pyrit.models import Message, MessagePiece
from pyrit.models.literals import ChatMessageRole


def _make_message(role: ChatMessageRole, content: str) -> Message:
    """Helper to create a Message from role and content."""
    return Message(message_pieces=[MessagePiece(role=role, original_value=content)])


# Get all aliases from the class
ALL_ALIASES = list(TokenizerTemplateNormalizer.MODEL_ALIASES.keys())


@pytest.mark.parametrize("alias", ALL_ALIASES)
@pytest.mark.asyncio
async def test_from_model_and_normalize(alias: str):
    """Test that each model alias can be loaded and normalize messages.

    Relies on HUGGINGFACE_TOKEN environment variable for gated models.
    """
    normalizer = TokenizerTemplateNormalizer.from_model(alias)

    assert normalizer is not None
    assert normalizer.tokenizer is not None
    assert normalizer.tokenizer.chat_template is not None

    messages = [
        _make_message("system", "You are a helpful assistant."),
        _make_message("user", "Hello!"),
    ]

    result = await normalizer.normalize_string_async(messages)

    assert result is not None
    assert len(result) > 0
    assert "Hello!" in result


@pytest.mark.asyncio
async def test_llama3_vision_with_image():
    """Test that Llama-3.2-Vision can handle multimodal content with images.

    Relies on HUGGINGFACE_TOKEN environment variable.
    """
    normalizer = TokenizerTemplateNormalizer.from_model("llama3-vision")

    # Use a real test image from the assets folder
    image_path = HOME_PATH / "assets" / "pyrit_architecture.png"

    # Create a message with both text and image pieces
    # Both pieces need the same conversation_id to be part of the same message
    conversation_id = "test-conversation"
    text_piece = MessagePiece(role="user", original_value="What is in this image?", conversation_id=conversation_id)
    image_piece = MessagePiece(
        role="user",
        original_value=str(image_path),
        converted_value_data_type="image_path",
        conversation_id=conversation_id,
    )
    message = Message(message_pieces=[text_piece, image_piece])

    result = await normalizer.normalize_string_async([message])

    assert result is not None
    assert len(result) > 0
    # The text should be in the output
    assert "What is in this image?" in result
