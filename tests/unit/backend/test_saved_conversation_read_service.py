# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Canonical saved-conversation pagination without attack reconstruction or writes."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.backend.services.saved_conversation_read_service import SavedConversationReadService
from pyrit.memory import PromptMemoryEntry, SQLiteMemory, set_message_piece_sha256_async
from pyrit.models import AttackResult, ConversationReference, ConversationType, MessagePiece


@pytest.fixture
async def saved_attack(sqlite_instance: SQLiteMemory) -> AttackResult:
    attack = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective="Saved objective",
        related_conversations={
            ConversationReference(conversation_id=str(uuid.uuid4()), conversation_type=kind)
            for kind in (ConversationType.PRUNED, ConversationType.ADVERSARIAL, ConversationType.PREPARATION)
        },
    )
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
    return attack


@pytest.fixture
async def saved_pieces(sqlite_instance: SQLiteMemory, saved_attack: AttackResult) -> list[MessagePiece]:
    pieces = [
        MessagePiece(
            id=uuid.UUID(int=index + 1),
            conversation_id=saved_attack.conversation_id,
            original_value=f"original-{index}",
            converted_value=f"converted-{index}",
            role="user" if index < 7 else "simulated_assistant",
            sequence=-1 if index < 7 else 0,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for index in range(10)
    ]
    for piece in pieces:
        await set_message_piece_sha256_async(piece)
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces)
    return pieces


async def test_reference_pages_include_only_main_and_pruned_without_message_hydration_async(
    sqlite_instance: SQLiteMemory, saved_attack: AttackResult, saved_pieces: list[MessagePiece]
) -> None:
    reader = SavedConversationReadService(memory=sqlite_instance)
    with (
        patch.object(sqlite_instance, "get_attack_results_async", new_callable=AsyncMock) as full_attacks,
        patch.object(sqlite_instance, "get_conversation_messages_async", new_callable=AsyncMock) as full_messages,
        patch.object(PromptMemoryEntry, "get_message_piece", autospec=True) as hydrate,
    ):
        first = await reader.list_conversations_async(attack_result_id=saved_attack.attack_result_id, limit=1)
        assert first is not None
        assert first.total_count == 2
        assert first.pagination is not None
        assert first.pagination.has_more
        assert first.pagination.next_cursor is not None
        second = await reader.list_conversations_async(
            attack_result_id=saved_attack.attack_result_id, limit=1, cursor=first.pagination.next_cursor
        )
        assert second is not None
        assert second.pagination is not None
        assert not second.pagination.has_more
        assert second.pagination.next_cursor is None
        hydrate.assert_not_called()
        full_attacks.assert_not_awaited()
        full_messages.assert_not_awaited()
    references = first.conversations + second.conversations
    assert [reference.conversation_id for reference in references] == [
        saved_attack.conversation_id,
        next(
            reference.conversation_id
            for reference in saved_attack.related_conversations
            if reference.conversation_type == ConversationType.PRUNED
        ),
    ]
    assert [reference.conversation_type for reference in references] == ["main", "pruned"]
    assert references[0].message_count == 2
    assert references[1].message_count == 0


async def test_piece_pages_continue_through_negative_sequences_and_multimodal_turns_async(
    sqlite_instance: SQLiteMemory, saved_attack: AttackResult, saved_pieces: list[MessagePiece]
) -> None:
    reader = SavedConversationReadService(memory=sqlite_instance)
    cursor = None
    returned: list[str] = []
    partial_sequences: list[list[int]] = []
    with (
        patch.object(sqlite_instance, "get_attack_results_async", new_callable=AsyncMock) as full_attacks,
        patch.object(sqlite_instance, "get_conversation_messages_async", new_callable=AsyncMock) as full_messages,
    ):
        while True:
            page = await reader.get_messages_async(
                attack_result_id=saved_attack.attack_result_id,
                conversation_id=saved_attack.conversation_id,
                limit=3,
                cursor=cursor,
            )
            assert page is not None
            pieces = [piece for message in page.messages for piece in message.message_pieces]
            assert len(pieces) <= 3
            returned.extend(str(piece.id) for piece in pieces)
            partial_sequences.append(page.partial_message_sequences)
            assert page.pagination is not None
            cursor = page.pagination.next_cursor
            if not page.pagination.has_more:
                break
        full_attacks.assert_not_awaited()
        full_messages.assert_not_awaited()
    assert returned == [str(piece.id) for piece in saved_pieces]
    assert len(set(returned)) == len(saved_pieces)
    assert partial_sequences == [[-1], [-1], [-1, 0], [0]]


async def test_message_reader_maps_only_the_requested_pieces_not_the_sentinel_async(
    sqlite_instance: SQLiteMemory, saved_pieces: list[MessagePiece]
) -> None:
    with patch.object(
        PromptMemoryEntry, "get_message_piece", autospec=True, side_effect=PromptMemoryEntry.get_message_piece
    ) as hydrate:
        page = await sqlite_instance.get_conversation_message_page_async(
            conversation_id=saved_pieces[0].conversation_id, limit=3
        )
    assert hydrate.call_count == 3
    assert page.next_cursor is not None
    assert page.next_cursor.sequence == -1
    assert page.partial_message_sequences == (-1,)
    assert [piece.id for message in page.messages for piece in message.message_pieces] == [
        piece.id for piece in saved_pieces[:3]
    ]


async def test_saved_cursors_reject_other_owners_and_changed_reference_order_async(
    sqlite_instance: SQLiteMemory, saved_attack: AttackResult, saved_pieces: list[MessagePiece]
) -> None:
    reader = SavedConversationReadService(memory=sqlite_instance)
    references = await reader.list_conversations_async(attack_result_id=saved_attack.attack_result_id, limit=1)
    assert references is not None
    assert references.pagination is not None
    reference_cursor = references.pagination.next_cursor
    assert reference_cursor is not None
    other_attack = AttackResult(conversation_id=str(uuid.uuid4()), objective="Other saved attack")
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[other_attack])
    with pytest.raises(ValueError, match="cursor"):
        await reader.list_conversations_async(attack_result_id=other_attack.attack_result_id, cursor=reference_cursor)
    message_page = await reader.get_messages_async(
        attack_result_id=saved_attack.attack_result_id, conversation_id=saved_attack.conversation_id, limit=1
    )
    assert message_page is not None
    assert message_page.pagination is not None
    with pytest.raises(ValueError, match="cursor"):
        await reader.get_messages_async(
            attack_result_id=saved_attack.attack_result_id,
            conversation_id=next(
                reference.conversation_id
                for reference in saved_attack.related_conversations
                if reference.conversation_type == ConversationType.PRUNED
            ),
            limit=1,
            cursor=message_page.pagination.next_cursor,
        )
    with pytest.raises(ValueError, match="not part of attack"):
        await reader.get_messages_async(
            attack_result_id=saved_attack.attack_result_id, conversation_id=other_attack.conversation_id
        )
    await sqlite_instance.update_attack_result_by_id_async(
        attack_result_id=saved_attack.attack_result_id,
        update_fields={
            "pruned_conversation_ids": [
                str(uuid.uuid4()),
                next(
                    reference.conversation_id
                    for reference in saved_attack.related_conversations
                    if reference.conversation_type == ConversationType.PRUNED
                ),
            ],
            "timestamp": saved_attack.timestamp,
        },
    )
    with pytest.raises(ValueError, match="cursor"):
        await reader.list_conversations_async(attack_result_id=saved_attack.attack_result_id, cursor=reference_cursor)
