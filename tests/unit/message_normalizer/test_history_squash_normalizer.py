# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.message_normalizer import HistorySquashNormalizer
from pyrit.models import Message, MessagePiece
from pyrit.models.literals import ChatMessageRole


def _make_message(role: ChatMessageRole, content: str) -> Message:
    return Message(message_pieces=[MessagePiece(role=role, original_value=content)])


async def test_history_squash_empty_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        await HistorySquashNormalizer().normalize_async(messages=[])


async def test_history_squash_single_message_returns_unchanged():
    messages = [_make_message("user", "hello")]
    result = await HistorySquashNormalizer().normalize_async(messages)
    assert len(result) == 1
    assert result[0].get_value() == "hello"
    assert result[0].api_role == "user"


async def test_history_squash_two_turns():
    messages = [
        _make_message("user", "hello"),
        _make_message("assistant", "hi there"),
        _make_message("user", "how are you?"),
    ]
    result = await HistorySquashNormalizer().normalize_async(messages)

    assert len(result) == 1
    assert result[0].api_role == "user"

    text = result[0].get_value()
    assert "[Conversation History]" in text
    assert "User: hello" in text
    assert "Assistant: hi there" in text
    assert "[Current Message]" in text
    assert "how are you?" in text


async def test_history_squash_includes_system_in_history():
    messages = [
        _make_message("system", "You are helpful"),
        _make_message("user", "hello"),
        _make_message("assistant", "hi"),
        _make_message("user", "bye"),
    ]
    result = await HistorySquashNormalizer().normalize_async(messages)

    assert len(result) == 1
    text = result[0].get_value()
    assert "System: You are helpful" in text
    assert "User: hello" in text
    assert "Assistant: hi" in text
    assert "[Current Message]" in text
    assert "bye" in text


async def test_history_squash_multi_piece_message():
    """Multi-piece last message has all pieces joined in [Current Message]."""
    conversation_id = "test-conv-id"
    pieces = [
        MessagePiece(role="user", original_value="part1", conversation_id=conversation_id),
        MessagePiece(role="user", original_value="part2", conversation_id=conversation_id),
    ]
    messages = [
        _make_message("assistant", "hi"),
        Message(message_pieces=pieces),
    ]
    result = await HistorySquashNormalizer().normalize_async(messages)

    text = result[0].get_value()
    assert "part1" in text
    assert "part2" in text


async def test_history_squash_preserves_original_list():
    """Normalize should not mutate the input list."""
    messages = [
        _make_message("user", "hello"),
        _make_message("assistant", "hi"),
        _make_message("user", "bye"),
    ]
    original_len = len(messages)
    await HistorySquashNormalizer().normalize_async(messages)
    assert len(messages) == original_len


async def test_history_squash_propagates_last_message_metadata():
    """
    Regression: the squashed piece must carry the last message's prompt_metadata
    so downstream normalizers (e.g. JsonSchemaNormalizer) still see request-level
    metadata such as the JSON schema key. Without propagation, the schema would
    be silently dropped when both MULTI_TURN and JSON_SCHEMA need adaptation.
    """
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    last_piece = MessagePiece(
        role="user",
        original_value="please score",
        prompt_metadata={"json_schema": schema, "scenario": "regression"},
    )
    messages = [
        _make_message("assistant", "earlier reply"),
        Message(message_pieces=[last_piece]),
    ]
    result = await HistorySquashNormalizer().normalize_async(messages)

    assert len(result) == 1
    squashed_piece = result[0].message_pieces[0]
    assert squashed_piece.prompt_metadata == {"json_schema": schema, "scenario": "regression"}
