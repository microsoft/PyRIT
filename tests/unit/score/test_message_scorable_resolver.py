# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import MagicMock

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import ContentEntryScorable, ContentScorable, Message, MessagePiece, MessageScorable, Score
from pyrit.score.message_scorable_resolver import MessageScorableResolver


def _stored_message(value: str = "stored response") -> Message:
    return MessagePiece(
        role="assistant",
        original_value=value,
        conversation_id=str(uuid.uuid4()),
    ).to_message()


async def test_resolver_reads_message_reference_from_memory(sqlite_instance: MemoryInterface):
    stored = _stored_message()
    (await sqlite_instance.add_message_to_memory_async(request=stored))

    resolved = await MessageScorableResolver().resolve_async(
        scorable=MessageScorable.from_message(stored),
        memory=sqlite_instance,
    )

    assert resolved.get_value() == "stored response"


async def test_resolver_reports_missing_piece_ids(sqlite_instance: MemoryInterface):
    stored = _stored_message()
    (await sqlite_instance.add_message_to_memory_async(request=stored))
    missing_id = uuid.uuid4()

    with pytest.raises(ValueError, match=f"No message pieces found in memory for ids \\['{missing_id}'\\]"):
        (
            await MessageScorableResolver().resolve_async(
                scorable=MessageScorable(message_piece_ids=(stored.get_piece().id, missing_id)),
                memory=sqlite_instance,
            )
        )


async def test_resolver_rejects_pieces_from_multiple_messages(sqlite_instance: MemoryInterface):
    conversation_id = str(uuid.uuid4())
    first = MessagePiece(
        role="user",
        original_value="ask",
        conversation_id=conversation_id,
        sequence=0,
    ).to_message()
    second = MessagePiece(
        role="assistant",
        original_value="answer",
        conversation_id=conversation_id,
        sequence=1,
    ).to_message()
    (await sqlite_instance.add_message_to_memory_async(request=first))
    (await sqlite_instance.add_message_to_memory_async(request=second))

    with pytest.raises(ValueError, match="exactly one message"):
        (
            await MessageScorableResolver().resolve_async(
                scorable=MessageScorable(
                    message_piece_ids=(first.get_piece().id, second.get_piece().id),
                ),
                memory=sqlite_instance,
            )
        )


async def test_resolver_preserves_reference_order(sqlite_instance: MemoryInterface):
    conversation_id = str(uuid.uuid4())
    first = MessagePiece(role="assistant", original_value="one", conversation_id=conversation_id, sequence=0)
    second = MessagePiece(role="assistant", original_value="two", conversation_id=conversation_id, sequence=0)
    (await sqlite_instance.add_message_to_memory_async(request=Message(message_pieces=[first, second])))

    resolved = await MessageScorableResolver().resolve_async(
        scorable=MessageScorable(message_piece_ids=(second.id, first.id)),
        memory=sqlite_instance,
    )

    assert [piece.original_value for piece in resolved.message_pieces] == ["two", "one"]


async def test_resolver_adapts_content_to_ephemeral_message():
    resolved = await MessageScorableResolver().resolve_async(
        scorable=ContentScorable(value="loose text"),
        memory=MagicMock(spec=MemoryInterface),
    )

    piece = resolved.get_piece()
    assert piece.converted_value == "loose text"
    assert piece.role == "user"
    assert piece.not_in_memory is True


async def test_resolver_reads_persisted_content_reference(sqlite_instance: MemoryInterface):
    score = Score(score_value="true", score_type="true_false", scorable=ContentScorable(value="stored loose text"))
    (await sqlite_instance.add_scores_to_memory_async(scores=[score]))
    assert isinstance(score.scorable, ContentEntryScorable)

    resolved = await MessageScorableResolver().resolve_async(scorable=score.scorable, memory=sqlite_instance)

    assert resolved.get_value() == "stored loose text"


async def test_resolver_reports_missing_content_reference(sqlite_instance: MemoryInterface):
    content_id = uuid.uuid4()

    with pytest.raises(ValueError, match=f"No stored scorable content found for id {content_id}"):
        (
            await MessageScorableResolver().resolve_async(
                scorable=ContentEntryScorable(content_id=content_id),
                memory=sqlite_instance,
            )
        )
