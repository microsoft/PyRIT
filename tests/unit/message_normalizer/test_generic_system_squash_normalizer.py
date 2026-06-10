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
