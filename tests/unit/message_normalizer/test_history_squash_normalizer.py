# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.message_normalizer import HistorySquashNormalizer, MessageStringNormalizer
from pyrit.models import JSON_SCHEMA_METADATA_KEY, Message, MessagePiece
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


def test_history_squash_rejects_invalid_expected_history_count():
    with pytest.raises(ValueError, match="expected_history_message_count must be at least 1"):
        HistorySquashNormalizer(expected_history_message_count=0)


async def test_history_squash_rejects_unexpected_history_count():
    messages = [
        _make_message("user", "history"),
        _make_message("user", "current"),
    ]

    with pytest.raises(ValueError, match="expected 2 history messages.*received 2 messages"):
        await HistorySquashNormalizer(expected_history_message_count=2).normalize_async(messages)


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


async def test_history_squash_uses_configured_formatter():
    formatter = MagicMock(spec=MessageStringNormalizer)
    formatter.normalize_string_async = AsyncMock(return_value="custom format")
    messages = [
        _make_message("user", "history"),
        _make_message("user", "current"),
    ]

    result = await HistorySquashNormalizer(
        message_normalizer=formatter,
        expected_history_message_count=1,
    ).normalize_async(messages)

    assert result[0].get_value() == "custom format"
    formatter.normalize_string_async.assert_awaited_once()


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


async def test_history_squash_preserves_original_and_converted_views():
    history = _make_message("user", "original history")
    history.get_piece().converted_value = "converted history"
    current = _make_message("user", "original current")
    current.get_piece().converted_value = "converted current"

    result = await HistorySquashNormalizer().normalize_async([history, current])

    piece = result[0].get_piece()
    assert "User: original history" in piece.original_value
    assert "original current" in piece.original_value
    assert "User: converted history" in piece.converted_value
    assert "converted current" in piece.converted_value


async def test_history_squash_preserves_live_multimodal_piece_order():
    conversation_id = "test-conv-id"
    current = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="diagram.png",
                original_value_data_type="image_path",
                conversation_id=conversation_id,
            ),
            MessagePiece(
                role="user",
                original_value="What does this show?",
                conversation_id=conversation_id,
            ),
        ]
    )

    result = await HistorySquashNormalizer().normalize_async([_make_message("assistant", "Earlier response"), current])

    pieces = result[0].message_pieces
    assert [piece.converted_value_data_type for piece in pieces] == ["image_path", "text"]
    assert pieces[0].converted_value == "diagram.png"
    assert "What does this show?" in pieces[1].converted_value
    assert "diagram.png" not in pieces[1].converted_value


async def test_history_squash_preserves_entirely_non_text_live_request():
    conversation_id = "test-conv-id"
    current = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="diagram.png",
                original_value_data_type="image_path",
                conversation_id=conversation_id,
            )
        ]
    )

    result = await HistorySquashNormalizer().normalize_async([_make_message("assistant", "Earlier response"), current])

    pieces = result[0].message_pieces
    assert [piece.converted_value_data_type for piece in pieces] == ["text", "image_path"]
    assert pieces[0].converted_value == "[Conversation History]\nAssistant: Earlier response"
    assert pieces[1].converted_value == "diagram.png"


async def test_history_squash_describes_non_text_history_without_exposing_path():
    history = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="C:\\private\\diagram.png",
                original_value_data_type="image_path",
                prompt_metadata={"context_description": "architecture diagram"},
            )
        ]
    )

    result = await HistorySquashNormalizer().normalize_async([history, _make_message("user", "What does it show?")])

    text = result[0].get_value()
    assert "User: [Image_path - architecture diagram]" in text
    assert "C:\\private\\diagram.png" not in text


async def test_history_squash_uses_live_text_metadata_for_combined_piece():
    schema = {"type": "object"}
    conversation_id = "test-conv-id"
    current = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="diagram.png",
                original_value_data_type="image_path",
                conversation_id=conversation_id,
            ),
            MessagePiece(
                role="user",
                original_value="Describe this image",
                conversation_id=conversation_id,
                prompt_metadata={JSON_SCHEMA_METADATA_KEY: schema},
            ),
        ]
    )

    result = await HistorySquashNormalizer().normalize_async([_make_message("assistant", "Earlier response"), current])

    text_piece = result[0].message_pieces[1]
    assert text_piece.converted_value_data_type == "text"
    assert text_piece.prompt_metadata == {JSON_SCHEMA_METADATA_KEY: schema}


async def test_history_squash_rejects_converted_non_text_history():
    history = _make_message("user", "original")
    history.get_piece().converted_value = "converted.png"
    history.get_piece().converted_value_data_type = "image_path"

    with pytest.raises(ValueError, match="non-text output types.*image_path"):
        await HistorySquashNormalizer().normalize_async([history, _make_message("user", "current")])


async def test_history_squash_preserves_original_list():
    """Normalize should not mutate the input messages."""
    messages = [
        _make_message("user", "hello"),
        _make_message("assistant", "hi"),
        _make_message("user", "bye"),
    ]
    original_messages = [message.model_copy(deep=True) for message in messages]

    await HistorySquashNormalizer().normalize_async(messages)

    assert messages == original_messages


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
