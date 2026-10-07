# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest

from pyrit.common.attack_result_scope import attack_result_id_scope, get_current_attack_result_id
from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import ConversationEntry
from pyrit.models import Conversation, ConversationRetryReason, Message, MessagePiece

ATTACK_A = str(uuid.uuid4())
ATTACK_B = str(uuid.uuid4())


async def _register_async(memory: MemoryInterface, conversation_id: str, **kwargs: str) -> None:
    await memory.add_conversation_to_memory_async(conversation=Conversation(conversation_id=conversation_id, **kwargs))


async def _add_turn_async(memory: MemoryInterface, conversation_id: str) -> None:
    for role, value in (("user", "question"), ("assistant", "answer")):
        await memory.add_message_to_memory_async(
            request=Message(
                message_pieces=[MessagePiece(role=role, original_value=value, conversation_id=conversation_id)]
            )
        )


async def _owner_async(memory: MemoryInterface, conversation_id: str) -> str | None:
    conversation = await memory.get_conversation_metadata_async(conversation_id=conversation_id)
    assert conversation is not None
    return conversation.attack_result_id


def test_scope_sets_and_restores_the_current_result_id() -> None:
    assert get_current_attack_result_id() is None
    with attack_result_id_scope(attack_result_id=ATTACK_A):
        assert get_current_attack_result_id() == ATTACK_A
        with attack_result_id_scope(attack_result_id=ATTACK_B):
            assert get_current_attack_result_id() == ATTACK_B
        assert get_current_attack_result_id() == ATTACK_A
    assert get_current_attack_result_id() is None


async def test_conversation_registered_outside_an_execution_has_no_owner(sqlite_instance: MemoryInterface) -> None:
    await _register_async(sqlite_instance, "conv-free")

    assert await _owner_async(sqlite_instance, "conv-free") is None


async def test_memory_does_not_infer_ownership_from_execution(sqlite_instance: MemoryInterface) -> None:
    with attack_result_id_scope(attack_result_id=ATTACK_A):
        await _register_async(sqlite_instance, "conv-a")

    assert await _owner_async(sqlite_instance, "conv-a") is None
    [entry] = sqlite_instance._query_entries(
        ConversationEntry, conditions=ConversationEntry.conversation_id == "conv-a"
    )
    assert entry.attack_result_id is None


async def test_explicit_owner_is_recorded(sqlite_instance: MemoryInterface) -> None:
    with attack_result_id_scope(attack_result_id=ATTACK_B):
        await _register_async(sqlite_instance, "conv-explicit", attack_result_id=ATTACK_A)

    assert await _owner_async(sqlite_instance, "conv-explicit") == ATTACK_A


async def test_registering_again_for_the_same_execution_is_a_no_op(sqlite_instance: MemoryInterface) -> None:
    await _register_async(sqlite_instance, "conv-same", attack_result_id=ATTACK_A)
    await _register_async(sqlite_instance, "conv-same", attack_result_id=ATTACK_A)
    await _register_async(sqlite_instance, "conv-same")

    assert await _owner_async(sqlite_instance, "conv-same") == ATTACK_A


async def test_conversation_cannot_be_assigned_to_a_different_execution(sqlite_instance: MemoryInterface) -> None:
    await _register_async(sqlite_instance, "conv-owned", attack_result_id=ATTACK_A)

    with pytest.raises(ValueError, match="cannot be assigned to attack result"):
        await _register_async(sqlite_instance, "conv-owned", attack_result_id=ATTACK_B)

    assert await _owner_async(sqlite_instance, "conv-owned") == ATTACK_A


async def test_unowned_conversation_is_claimed_by_the_first_execution(sqlite_instance: MemoryInterface) -> None:
    await _register_async(sqlite_instance, "conv-unowned")

    await _register_async(sqlite_instance, "conv-unowned", attack_result_id=ATTACK_A)

    assert await _owner_async(sqlite_instance, "conv-unowned") == ATTACK_A


async def test_copies_preserve_or_explicitly_replace_the_owner(sqlite_instance: MemoryInterface) -> None:
    with attack_result_id_scope(attack_result_id=ATTACK_A):
        await _register_async(sqlite_instance, "conv-source", attack_result_id=ATTACK_A)
        await _add_turn_async(sqlite_instance, "conv-source")
        await _add_turn_async(sqlite_instance, "conv-source")
        same_execution_copy = await sqlite_instance.duplicate_conversation_async(conversation_id="conv-source")
        same_execution_trimmed = await sqlite_instance.duplicate_conversation_excluding_last_turn_async(
            conversation_id="conv-source"
        )
    with attack_result_id_scope(attack_result_id=ATTACK_B):
        new_execution_copy = await sqlite_instance.duplicate_conversation_async(
            conversation_id="conv-source", attack_result_id=ATTACK_B
        )
    unscoped_copy = await sqlite_instance.duplicate_conversation_async(conversation_id="conv-source")

    assert await _owner_async(sqlite_instance, same_execution_copy) == ATTACK_A
    assert await _owner_async(sqlite_instance, same_execution_trimmed) == ATTACK_A
    assert await _owner_async(sqlite_instance, new_execution_copy) == ATTACK_B
    assert await _owner_async(sqlite_instance, unscoped_copy) == ATTACK_A
    assert await _owner_async(sqlite_instance, "conv-source") == ATTACK_A


async def test_retry_record_created_during_an_execution_is_linked(sqlite_instance: MemoryInterface) -> None:
    await _register_async(sqlite_instance, "conv-retry", attack_result_id=ATTACK_A)
    with attack_result_id_scope(attack_result_id=ATTACK_A):
        await sqlite_instance.add_conversation_retry_async(
            conversation_id="conv-retry", sequence=1, reason=ConversationRetryReason.JSON_PARSING
        )

    assert await _owner_async(sqlite_instance, "conv-retry") == ATTACK_A


async def test_conversations_and_pieces_are_queryable_by_result_id(sqlite_instance: MemoryInterface) -> None:
    await _register_async(sqlite_instance, "conv-a-2", attack_result_id=ATTACK_A)
    await _register_async(sqlite_instance, "conv-a-1", attack_result_id=ATTACK_A)
    await _register_async(sqlite_instance, "conv-b", attack_result_id=ATTACK_B)
    for conversation_id in ("conv-a-1", "conv-a-2", "conv-b"):
        await _add_turn_async(sqlite_instance, conversation_id)

    conversations = await sqlite_instance.get_attack_result_conversations_async(attack_result_id=ATTACK_A)
    assert [conversation.conversation_id for conversation in conversations] == ["conv-a-1", "conv-a-2"]
    assert {conversation.attack_result_id for conversation in conversations} == {ATTACK_A}

    pieces = await sqlite_instance.get_message_pieces_async(attack_result_id=ATTACK_A)
    assert len(pieces) == 4
    assert {piece.conversation_id for piece in pieces} == {"conv-a-1", "conv-a-2"}
    requests = await sqlite_instance.get_message_pieces_async(attack_result_id=ATTACK_B, role="user")
    assert [(piece.conversation_id, piece.original_value) for piece in requests] == [("conv-b", "question")]
    assert await sqlite_instance.get_attack_result_conversations_async(attack_result_id=str(uuid.uuid4())) == []
