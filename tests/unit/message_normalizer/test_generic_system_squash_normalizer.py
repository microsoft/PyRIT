# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.message_normalizer import GenericSystemSquashNormalizer
from pyrit.models import Message, MessagePiece
from pyrit.models.literals import ChatMessageRole


def _make_message(role: ChatMessageRole, content: str) -> Message:
    """Helper to create a Message from role and content."""
    return Message(message_pieces=[MessagePiece(role=role, original_value=content)])


async def test_generic_squash_system_message():
    messages = [
        _make_message("system", "System message"),
        _make_message("user", "User message 1"),
        _make_message("assistant", "Assistant message"),
    ]
    result = await GenericSystemSquashNormalizer().normalize_async(messages)
    assert len(result) == 2
    assert result[0].api_role == "user"
    assert result[0].get_value() == "### Instructions ###\n\nSystem message\n\n######\n\nUser message 1"
    assert result[1].api_role == "assistant"
    assert result[1].get_value() == "Assistant message"


async def test_generic_squash_system_message_empty_list():
    with pytest.raises(ValueError):
        await GenericSystemSquashNormalizer().normalize_async(messages=[])


async def test_generic_squash_system_message_single_system_message():
    messages = [_make_message("system", "System message")]
    result = await GenericSystemSquashNormalizer().normalize_async(messages)
    assert len(result) == 1
    assert result[0].api_role == "user"
    assert result[0].get_value() == "System message"


async def test_generic_squash_system_message_no_system_message():
    messages = [_make_message("user", "User message 1"), _make_message("user", "User message 2")]
    result = await GenericSystemSquashNormalizer().normalize_async(messages)
    assert len(result) == 2
    assert result[0].api_role == "user"
    assert result[0].get_value() == "User message 1"
    assert result[1].api_role == "user"
    assert result[1].get_value() == "User message 2"


async def test_generic_squash_normalize_to_dicts_async():
    """Test that normalize_to_dicts_async returns list of dicts with Message.to_dict() format."""
    messages = [
        _make_message("system", "System message"),
        _make_message("user", "User message"),
    ]
    result = await GenericSystemSquashNormalizer().normalize_to_dicts_async(messages)

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], dict)
    assert result[0]["role"] == "user"
    assert "pieces" in result[0]
    assert len(result[0]["pieces"]) == 1
    assert "### Instructions ###" in result[0]["pieces"][0]["converted_value"]
    assert "System message" in result[0]["pieces"][0]["converted_value"]
    assert "User message" in result[0]["pieces"][0]["converted_value"]


async def test_generic_squash_preserves_multipart_user_message():
    """Test that squashing keeps non-text user pieces instead of collapsing to plain text."""
    conversation_id = "conv-1"
    messages = [
        _make_message("system", "System message"),
        Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="User message",
                    conversation_id=conversation_id,
                    sequence=0,
                ),
                MessagePiece(
                    role="user",
                    original_value="/tmp/example.png",
                    original_value_data_type="image_path",
                    conversation_id=conversation_id,
                    sequence=0,
                ),
            ]
        ),
    ]

    result = await GenericSystemSquashNormalizer().normalize_async(messages)

    assert len(result) == 1
    assert result[0].api_role == "user"
    assert len(result[0].message_pieces) == 2
    assert result[0].get_value() == "### Instructions ###\n\nSystem message\n\n######\n\nUser message"
    assert result[0].message_pieces[1].converted_value == "/tmp/example.png"
    assert result[0].message_pieces[1].converted_value_data_type == "image_path"


async def test_generic_squash_uses_first_user_message_instead_of_rewriting_assistant():
    """Test that squash targets the first user message even if assistant messages appear first."""
    messages = [
        _make_message("system", "System message"),
        _make_message("assistant", "Assistant message"),
        _make_message("user", "User message"),
    ]

    result = await GenericSystemSquashNormalizer().normalize_async(messages)

    assert len(result) == 2
    assert result[0].api_role == "assistant"
    assert result[0].get_value() == "Assistant message"
    assert result[1].api_role == "user"
    assert result[1].get_value() == "### Instructions ###\n\nSystem message\n\n######\n\nUser message"


async def test_generic_squash_no_user_message_converts_system_to_user():
    """Test that system is converted to user when no user messages exist."""
    messages = [
        _make_message("system", "System message"),
        _make_message("assistant", "Assistant message"),
    ]

    result = await GenericSystemSquashNormalizer().normalize_async(messages)

    assert len(result) == 2
    assert result[0].api_role == "user"
    assert result[0].get_value() == "System message"
    assert result[1].api_role == "assistant"
    assert result[1].get_value() == "Assistant message"


async def test_generic_squash_preserves_image_first_multipart_user_message():
    """Test that squashing merges into the first text piece when an image piece comes first."""
    conversation_id = "conv-image-first"
    messages = [
        _make_message("system", "System message"),
        Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="/tmp/example.png",
                    original_value_data_type="image_path",
                    conversation_id=conversation_id,
                    sequence=0,
                ),
                MessagePiece(
                    role="user",
                    original_value="Describe this image",
                    conversation_id=conversation_id,
                    sequence=0,
                ),
            ]
        ),
    ]

    result = await GenericSystemSquashNormalizer().normalize_async(messages)

    assert len(result) == 1
    assert result[0].api_role == "user"
    assert len(result[0].message_pieces) == 2
    assert result[0].message_pieces[0].converted_value == "/tmp/example.png"
    assert result[0].message_pieces[0].converted_value_data_type == "image_path"
    assert result[0].message_pieces[1].converted_value_data_type == "text"
    assert (
        result[0].message_pieces[1].converted_value
        == "### Instructions ###\n\nSystem message\n\n######\n\nDescribe this image"
    )


async def test_generic_squash_user_message_without_text_pieces_prepends_instructions():
    """Test that an instruction-only text piece is prepended when no text piece exists to merge into."""
    conversation_id = "conv-no-text"
    messages = [
        _make_message("system", "System message"),
        Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="/tmp/example.png",
                    original_value_data_type="image_path",
                    conversation_id=conversation_id,
                    sequence=0,
                ),
            ]
        ),
    ]

    result = await GenericSystemSquashNormalizer().normalize_async(messages)

    assert len(result) == 1
    assert result[0].api_role == "user"
    assert len(result[0].message_pieces) == 2
    assert result[0].message_pieces[0].converted_value_data_type == "text"
    assert result[0].message_pieces[0].converted_value == "### Instructions ###\n\nSystem message\n\n######"
    assert result[0].message_pieces[1].converted_value == "/tmp/example.png"
    assert result[0].message_pieces[1].converted_value_data_type == "image_path"


async def test_generic_squash_propagates_user_piece_metadata():
    """
    Regression: when squashing system + user, the squashed piece must carry the
    user piece's prompt_metadata so downstream normalizers (e.g.
    JsonSchemaNormalizer) still see request-level metadata. Without propagation,
    the schema would be silently dropped when both SYSTEM_PROMPT and JSON_SCHEMA
    need adaptation.
    """
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    user_piece = MessagePiece(
        role="user",
        original_value="please score",
        prompt_metadata={"json_schema": schema, "scenario": "regression"},
    )
    messages = [
        _make_message("system", "system prompt"),
        Message(message_pieces=[user_piece]),
    ]
    result = await GenericSystemSquashNormalizer().normalize_async(messages)

    assert len(result) == 1
    squashed_piece = result[0].message_pieces[0]
    assert squashed_piece.prompt_metadata == {"json_schema": schema, "scenario": "regression"}


async def test_generic_squash_single_system_propagates_metadata():
    """
    When only a system message is present it is converted to a user message; its
    prompt_metadata must be preserved for the same reason as the squash case.
    """
    schema = {"type": "object"}
    system_piece = MessagePiece(
        role="system",
        original_value="system prompt",
        prompt_metadata={"json_schema": schema},
    )
    result = await GenericSystemSquashNormalizer().normalize_async([Message(message_pieces=[system_piece])])

    assert len(result) == 1
    converted_piece = result[0].message_pieces[0]
    assert converted_piece.prompt_metadata == {"json_schema": schema}
