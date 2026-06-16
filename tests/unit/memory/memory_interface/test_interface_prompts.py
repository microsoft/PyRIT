# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import uuid
from collections.abc import MutableSequence, Sequence
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from unit.mocks import get_mock_target

from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.memory import MemoryInterface, PromptMemoryEntry
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackResult,
    ComponentIdentifier,
    Conversation,
    IdentifierFilter,
    IdentifierType,
    Message,
    MessagePiece,
    Score,
    SeedPrompt,
)


def _test_scorer_id(name: str = "TestScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="tests.unit.memory",
    )


def assert_original_value_in_list(original_value: str, message_pieces: Sequence[MessagePiece]):
    for piece in message_pieces:
        if piece.original_value == original_value:
            return True
    raise AssertionError(f"Original value {original_value} not found in list")


def test_conversation_memory_empty_by_default(sqlite_instance: MemoryInterface):
    expected_count = 0
    c = sqlite_instance.get_message_pieces()
    assert len(c) == expected_count


@pytest.mark.parametrize("num_conversations", [1, 2, 3])
def test_add_message_pieces_to_memory(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece], num_conversations: int
):
    for c in sample_conversations[:num_conversations]:
        c.conversation_id = sample_conversations[0].conversation_id
        c.role = sample_conversations[0].role
        c.sequence = 0

    message = Message(message_pieces=sample_conversations[:num_conversations])

    sqlite_instance.add_message_to_memory(request=message)
    assert len(sqlite_instance.get_message_pieces()) == num_conversations


def test_get_message_pieces_uuid_and_string_ids(sqlite_instance: MemoryInterface):
    """Test that get_message_pieces handles both UUID objects and string representations."""
    uuid1 = uuid.uuid4()
    uuid2 = uuid.uuid4()
    uuid3 = uuid.uuid4()

    pieces = [
        MessagePiece(
            conversation_id=str(uuid4()),
            id=uuid1,
            role="user",
            original_value="Test prompt 1",
            converted_value="Test prompt 1",
        ),
        MessagePiece(
            conversation_id=str(uuid4()),
            id=uuid2,
            role="assistant",
            original_value="Test prompt 2",
            converted_value="Test prompt 2",
        ),
        MessagePiece(
            conversation_id=str(uuid4()),
            id=uuid3,
            role="user",
            original_value="Test prompt 3",
            converted_value="Test prompt 3",
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

    uuid_results = sqlite_instance.get_message_pieces(prompt_ids=[uuid1, uuid2])
    assert len(uuid_results) == 2
    assert {str(uuid1), str(uuid2)} == {str(piece.id) for piece in uuid_results}

    str_results = sqlite_instance.get_message_pieces(prompt_ids=[str(uuid1), str(uuid2)])
    assert len(str_results) == 2
    assert {str(uuid1), str(uuid2)} == {str(piece.id) for piece in str_results}

    mixed_types: Sequence[str | uuid.UUID] = [uuid1, str(uuid2)]
    mixed_results = sqlite_instance.get_message_pieces(prompt_ids=mixed_types)
    assert len(mixed_results) == 2
    assert {str(uuid1), str(uuid2)} == {str(piece.id) for piece in mixed_results}

    single_uuid_result = sqlite_instance.get_message_pieces(prompt_ids=[uuid3])
    assert len(single_uuid_result) == 1
    assert str(single_uuid_result[0].id) == str(uuid3)

    single_str_result = sqlite_instance.get_message_pieces(prompt_ids=[str(uuid3)])
    assert len(single_str_result) == 1
    assert str(single_str_result[0].id) == str(uuid3)


def test_get_message_pieces_empty_prompt_ids_returns_empty(sqlite_instance: MemoryInterface):
    piece = MessagePiece(
        conversation_id=str(uuid4()),
        id=uuid.uuid4(),
        role="user",
        original_value="Test prompt",
        converted_value="Test prompt",
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[piece])

    assert sqlite_instance.get_message_pieces(prompt_ids=[]) == []


def test_duplicate_memory(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    attack2 = PromptSendingAttack(objective_target=get_mock_target("Target2"))
    conversation_id_1 = "11111"
    conversation_id_2 = "22222"
    conversation_id_3 = "33333"
    pieces = [
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id_1,
            sequence=0,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id_1,
            sequence=1,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id_3,
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id_2,
            sequence=0,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id_2,
            sequence=1,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
    assert len(sqlite_instance.get_message_pieces()) == 5
    new_conversation_id1 = sqlite_instance.duplicate_conversation(
        conversation_id=conversation_id_1,
    )
    new_conversation_id2 = sqlite_instance.duplicate_conversation(
        conversation_id=conversation_id_2,
    )
    all_pieces = sqlite_instance.get_message_pieces()
    assert len(all_pieces) == 9
    assert len([p for p in all_pieces if p.conversation_id == conversation_id_1]) == 2
    assert len([p for p in all_pieces if p.conversation_id == conversation_id_2]) == 2
    assert len([p for p in all_pieces if p.conversation_id == conversation_id_3]) == 1
    assert len([p for p in all_pieces if p.conversation_id == new_conversation_id1]) == 2
    assert len([p for p in all_pieces if p.conversation_id == new_conversation_id2]) == 2


# Ensure that the score entries are not duplicated when a conversation is duplicated
def test_duplicate_conversation_pieces_not_score(sqlite_instance: MemoryInterface):
    conversation_id = str(uuid4())
    prompt_id_1 = uuid4()
    prompt_id_2 = uuid4()
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    memory_labels = {"sample": "label"}
    pieces = [
        MessagePiece(
            id=prompt_id_1,
            role="assistant",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id,
            sequence=0,
            labels=memory_labels,
        ),
        MessagePiece(
            id=prompt_id_2,
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id,
            sequence=0,
            labels=memory_labels,
        ),
    ]
    # Ensure that the original prompt id defaults to the id of the piece
    assert pieces[0].original_prompt_id == pieces[0].id
    assert pieces[1].original_prompt_id == pieces[1].id
    scores = [
        Score(
            score_value=str(0.8),
            score_value_description="High score",
            score_type="float_scale",
            score_category=["test"],
            score_rationale="Test score",
            score_metadata={"test": "metadata"},
            scorer_class_identifier=_test_scorer_id("TestScorer1"),
            message_piece_id=prompt_id_1,
        ),
        Score(
            score_value=str(0.5),
            score_value_description="High score",
            score_type="float_scale",
            score_category=["test"],
            score_rationale="Test score",
            score_metadata={"test": "metadata"},
            scorer_class_identifier=_test_scorer_id("TestScorer2"),
            message_piece_id=prompt_id_2,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
    sqlite_instance.add_scores_to_memory(scores=scores)
    new_conversation_id = sqlite_instance.duplicate_conversation(
        conversation_id=conversation_id,
    )
    new_pieces = sqlite_instance.get_message_pieces(conversation_id=new_conversation_id)
    new_pieces_ids = [str(p.id) for p in new_pieces]
    assert len(new_pieces) == 2
    original_ids = {piece.original_prompt_id for piece in new_pieces}
    assert original_ids == {prompt_id_1, prompt_id_2}

    for piece in new_pieces:
        assert piece.id not in (prompt_id_1, prompt_id_2)
    assert len(sqlite_instance.get_prompt_scores(labels=memory_labels)) == 2

    # The duplicate prompts ids should not have scores so only two scores are returned
    assert len(sqlite_instance.get_prompt_scores(prompt_ids=[str(prompt_id_1), str(prompt_id_2)] + new_pieces_ids)) == 2


def test_duplicate_conversation_excluding_last_turn(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    attack2 = PromptSendingAttack(objective_target=get_mock_target())
    conversation_id_1 = "11111"
    conversation_id_2 = "22222"
    pieces = [
        MessagePiece(
            role="user",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=0,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=1,
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            sequence=2,
            conversation_id=conversation_id_1,
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id_2,
            sequence=2,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id_2,
            sequence=3,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
    assert len(sqlite_instance.get_message_pieces()) == 5

    new_conversation_id1 = sqlite_instance.duplicate_conversation_excluding_last_turn(
        conversation_id=conversation_id_1,
    )

    all_memory = sqlite_instance.get_message_pieces()
    assert len(all_memory) == 7

    duplicate_conversation = sqlite_instance.get_message_pieces(conversation_id=new_conversation_id1)
    assert len(duplicate_conversation) == 2

    for piece in duplicate_conversation:
        assert piece.sequence < 2


def test_duplicate_conversation_excluding_last_turn_not_score(sqlite_instance: MemoryInterface):
    conversation_id = str(uuid4())
    prompt_id_1 = uuid4()
    prompt_id_2 = uuid4()
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    memory_labels = {"sample": "label"}
    pieces = [
        MessagePiece(
            id=prompt_id_1,
            role="user",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id,
            sequence=0,
            labels=memory_labels,
        ),
        MessagePiece(
            id=prompt_id_2,
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id,
            sequence=1,
            labels=memory_labels,
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="That's good.",
            conversation_id=conversation_id,
            sequence=2,
            labels=memory_labels,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="Thanks.",
            conversation_id=conversation_id,
            sequence=3,
            labels=memory_labels,
        ),
    ]
    # Ensure that the original prompt id defaults to the id of the piece
    assert pieces[0].original_prompt_id == pieces[0].id
    assert pieces[1].original_prompt_id == pieces[1].id
    scores = [
        Score(
            score_value=str(0.8),
            score_value_description="High score",
            score_type="float_scale",
            score_category=["test"],
            score_rationale="Test score",
            score_metadata={"test": "metadata"},
            scorer_class_identifier=_test_scorer_id("TestScorer1"),
            message_piece_id=prompt_id_1,
        ),
        Score(
            score_value=str(0.5),
            score_value_description="High score",
            score_type="float_scale",
            score_category=["test"],
            score_rationale="Test score",
            score_metadata={"test": "metadata"},
            scorer_class_identifier=_test_scorer_id("TestScorer2"),
            message_piece_id=prompt_id_2,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
    sqlite_instance.add_scores_to_memory(scores=scores)

    new_conversation_id = sqlite_instance.duplicate_conversation_excluding_last_turn(
        conversation_id=conversation_id,
    )
    new_pieces = sqlite_instance.get_message_pieces(conversation_id=new_conversation_id)
    new_pieces_ids = [str(p.id) for p in new_pieces]
    assert len(new_pieces) == 2
    assert new_pieces[0].original_prompt_id == prompt_id_1
    assert new_pieces[1].original_prompt_id == prompt_id_2
    assert new_pieces[0].id != prompt_id_1
    assert new_pieces[1].id != prompt_id_2
    assert len(sqlite_instance.get_prompt_scores(labels=memory_labels)) == 2
    # The duplicate prompts ids should not have scores so only two scores are returned
    assert len(sqlite_instance.get_prompt_scores(prompt_ids=[str(prompt_id_1), str(prompt_id_2)] + new_pieces_ids)) == 2


def test_duplicate_conversation_excluding_last_turn_same_attack(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    conversation_id_1 = "11111"
    pieces = [
        MessagePiece(
            role="user",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=0,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=1,
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=2,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=3,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
    assert len(sqlite_instance.get_message_pieces()) == 4

    new_conversation_id1 = sqlite_instance.duplicate_conversation_excluding_last_turn(
        conversation_id=conversation_id_1,
    )

    all_memory = sqlite_instance.get_message_pieces()
    assert len(all_memory) == 6

    duplicate_conversation = sqlite_instance.get_message_pieces(conversation_id=new_conversation_id1)
    assert len(duplicate_conversation) == 2

    for piece in duplicate_conversation:
        assert piece.sequence < 2


def test_duplicate_conversation_creates_new_ids(sqlite_instance: MemoryInterface):
    """Test that duplicated conversation has new piece IDs."""
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    conversation_id = "test-conv-123"
    original_piece = MessagePiece(
        role="user",
        original_value="original prompt text",
        converted_value="Hello",
        conversation_id=conversation_id,
        sequence=1,
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[original_piece])

    new_conversation_id = sqlite_instance.duplicate_conversation(
        conversation_id=conversation_id,
    )

    original_pieces = sqlite_instance.get_message_pieces(conversation_id=conversation_id)
    new_pieces = sqlite_instance.get_message_pieces(conversation_id=new_conversation_id)

    assert len(original_pieces) == 1
    assert len(new_pieces) == 1

    # IDs should be different
    assert original_pieces[0].id != new_pieces[0].id

    # Content should be preserved
    assert original_pieces[0].original_value == new_pieces[0].original_value
    assert original_pieces[0].converted_value == new_pieces[0].converted_value


def test_duplicate_conversation_preserves_original_prompt_id(sqlite_instance: MemoryInterface):
    """Test that duplicated conversation preserves original_prompt_id for tracing."""
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    conversation_id = "test-conv-456"
    original_piece = MessagePiece(
        role="user",
        original_value="traceable prompt",
        conversation_id=conversation_id,
        sequence=1,
    )
    sqlite_instance.add_message_pieces_to_memory(message_pieces=[original_piece])
    original_prompt_id = original_piece.original_prompt_id

    new_conversation_id = sqlite_instance.duplicate_conversation(
        conversation_id=conversation_id,
    )

    new_pieces = sqlite_instance.get_message_pieces(conversation_id=new_conversation_id)

    # original_prompt_id should be preserved for tracing
    assert new_pieces[0].original_prompt_id == original_prompt_id


def test_duplicate_conversation_with_multiple_pieces(sqlite_instance: MemoryInterface):
    """Test that duplicating a multi-piece conversation works correctly."""
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    conversation_id = "multi-piece-conv"

    pieces = [
        MessagePiece(
            role="user",
            original_value="user message 1",
            conversation_id=conversation_id,
            sequence=1,
        ),
        MessagePiece(
            role="assistant",
            original_value="assistant response 1",
            conversation_id=conversation_id,
            sequence=2,
        ),
        MessagePiece(
            role="user",
            original_value="user message 2",
            conversation_id=conversation_id,
            sequence=3,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

    new_conversation_id = sqlite_instance.duplicate_conversation(
        conversation_id=conversation_id,
    )

    original_pieces = sqlite_instance.get_message_pieces(conversation_id=conversation_id)
    new_pieces = sqlite_instance.get_message_pieces(conversation_id=new_conversation_id)

    assert len(new_pieces) == 3

    # All pieces should have unique IDs
    all_ids = {p.id for p in original_pieces} | {p.id for p in new_pieces}
    assert len(all_ids) == 6

    # Sequences and roles should be preserved
    for orig, new in zip(
        sorted(original_pieces, key=lambda p: p.sequence), sorted(new_pieces, key=lambda p: p.sequence), strict=False
    ):
        assert orig.sequence == new.sequence
        assert orig.api_role == new.api_role
        assert orig.original_value == new.original_value


def test_add_message_pieces_to_memory_calls_validate(sqlite_instance: MemoryInterface):
    message = MagicMock(Message)
    message.message_pieces = [MagicMock(MessagePiece, not_in_memory=False, conversation_id="test-conversation")]
    with (
        patch("pyrit.memory.sqlite_memory.SQLiteMemory.add_message_pieces_to_memory"),
        patch("pyrit.memory.memory_interface.MemoryInterface._update_sequence"),
    ):
        sqlite_instance.add_message_to_memory(request=message)
    assert message.validate.called


@pytest.mark.parametrize("bad_id", [None, "", "   "])
def test_add_message_pieces_to_memory_raises_when_conversation_id_missing(sqlite_instance: MemoryInterface, bad_id):
    piece = MessagePiece(role="user", original_value="hello", conversation_id=bad_id)
    with pytest.raises(ValueError, match="conversation_id"):
        sqlite_instance.add_message_pieces_to_memory(message_pieces=[piece])


@pytest.mark.parametrize("bad_id", [None, "", "   "])
def test_add_message_to_memory_raises_when_conversation_id_missing(sqlite_instance: MemoryInterface, bad_id):
    piece = MessagePiece(role="user", original_value="hello", conversation_id=bad_id)
    with pytest.raises(ValueError, match="conversation_id"):
        sqlite_instance.add_message_to_memory(request=Message(message_pieces=[piece]))


def test_add_message_pieces_to_memory_skips_not_in_memory_without_conversation_id(
    sqlite_instance: MemoryInterface,
):
    # not_in_memory pieces are filtered out before persistence, so a missing
    # conversation_id on an ephemeral piece must not raise.
    ephemeral = MessagePiece(role="user", original_value="ephemeral", conversation_id=None)
    ephemeral.not_in_memory = True

    sqlite_instance.add_message_pieces_to_memory(message_pieces=[ephemeral])

    assert sqlite_instance.get_message_pieces() == []


def test_add_conversation_to_memory_records_target_for_plain_message_writes(sqlite_instance: MemoryInterface):
    # Registering a conversation records its target once; subsequent message writes
    # do not take a target, yet target-filtered reads still find the messages.
    target_id = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://api.openai.com", "model_name": "gpt-4"},
    )
    conversation_id = "conv-registered"
    sqlite_instance.add_conversation_to_memory(
        conversation=Conversation(conversation_id=conversation_id, target_identifier=target_id)
    )
    sqlite_instance.add_message_pieces_to_memory(
        message_pieces=[MessagePiece(role="user", original_value="hi", conversation_id=conversation_id)]
    )

    metadata = sqlite_instance._get_conversation(conversation_id=conversation_id)
    assert metadata is not None
    assert metadata.target_identifier.hash == target_id.hash

    results = sqlite_instance.get_message_pieces(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.TARGET,
                property_path="$.hash",
                value=target_id.hash,
                partial_match=False,
            )
        ],
    )
    assert len(results) == 1
    assert results[0].conversation_id == conversation_id


def test_message_writes_without_registration_create_no_conversation_row(sqlite_instance: MemoryInterface):
    # Message writes no longer touch the Conversations table; conversation metadata
    # exists only when a conversation is explicitly registered.
    conversation_id = "conv-unregistered"
    sqlite_instance.add_message_pieces_to_memory(
        message_pieces=[MessagePiece(role="user", original_value="hi", conversation_id=conversation_id)]
    )

    assert sqlite_instance._get_conversation(conversation_id=conversation_id) is None
    # The messages themselves still persist.
    assert len(sqlite_instance.get_message_pieces(conversation_id=conversation_id)) == 1


def test_add_conversation_to_memory_same_target_reregister_is_noop(sqlite_instance: MemoryInterface):
    # A conversation is held with exactly one target. Re-registering the same
    # conversation with the same target is idempotent (no error, no change) so that
    # per-turn registration during a multi-turn conversation is safe.
    conversation_id = "conv-reregister-same"
    target = ComponentIdentifier(
        class_name="OpenAIChatTarget", class_module="pyrit.prompt_target", params={"endpoint": "a"}
    )
    sqlite_instance.add_conversation_to_memory(
        conversation=Conversation(conversation_id=conversation_id, target_identifier=target)
    )
    sqlite_instance.add_conversation_to_memory(
        conversation=Conversation(conversation_id=conversation_id, target_identifier=target)
    )

    metadata = sqlite_instance._get_conversation(conversation_id=conversation_id)
    assert metadata is not None
    assert metadata.target_identifier.hash == target.hash


def test_add_conversation_to_memory_different_target_reregister_raises(sqlite_instance: MemoryInterface):
    # A conversation is held with exactly one target, so re-registering an existing
    # conversation_id with a different target is a conflict and must raise rather than
    # silently re-targeting the conversation.
    conversation_id = "conv-retarget"
    target_a = ComponentIdentifier(
        class_name="OpenAIChatTarget", class_module="pyrit.prompt_target", params={"endpoint": "a"}
    )
    target_b = ComponentIdentifier(
        class_name="OpenAIChatTarget", class_module="pyrit.prompt_target", params={"endpoint": "b"}
    )
    sqlite_instance.add_conversation_to_memory(
        conversation=Conversation(conversation_id=conversation_id, target_identifier=target_a)
    )
    with pytest.raises(ValueError, match="already registered with a different target"):
        sqlite_instance.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_b)
        )

    # The originally recorded target is left untouched.
    metadata = sqlite_instance._get_conversation(conversation_id=conversation_id)
    assert metadata is not None
    assert metadata.target_identifier.hash == target_a.hash


def test_insert_conversation_rolls_back_and_reraises_on_db_error(sqlite_instance: MemoryInterface):
    # A DB failure during registration rolls back the session and propagates the error
    # rather than leaving a half-written Conversations row.
    from sqlalchemy.exc import SQLAlchemyError

    session = MagicMock()
    session.get.side_effect = SQLAlchemyError("boom")

    with patch.object(sqlite_instance, "get_session", return_value=session):
        with pytest.raises(SQLAlchemyError, match="boom"):
            sqlite_instance._insert_conversation(conversation=Conversation(conversation_id="conv-fail"))

    session.rollback.assert_called_once()
    session.commit.assert_not_called()


def test_add_message_pieces_to_memory_updates_sequence(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    for conversation in sample_conversations:
        conversation.conversation_id = sample_conversations[0].conversation_id
        conversation.role = sample_conversations[0].role
        conversation.sequence = 17

    with patch("pyrit.memory.sqlite_memory.SQLiteMemory.add_message_pieces_to_memory") as mock_add:
        sqlite_instance.add_message_to_memory(request=Message(message_pieces=sample_conversations))
        assert mock_add.called

        args, kwargs = mock_add.call_args
        assert kwargs["message_pieces"][0].sequence == 0, "Sequence should be reset to 0"
        assert kwargs["message_pieces"][1].sequence == 0, "Sequence should be reset to 0"
        assert kwargs["message_pieces"][2].sequence == 0, "Sequence should be reset to 0"


def test_add_message_pieces_to_memory_updates_sequence_with_prev_conversation(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    for conversation in sample_conversations:
        conversation.conversation_id = sample_conversations[0].conversation_id
        conversation.role = sample_conversations[0].role
        conversation.sequence = 17

    # insert one of these into memory
    sqlite_instance.add_message_to_memory(request=Message(message_pieces=sample_conversations))

    with patch("pyrit.memory.sqlite_memory.SQLiteMemory.add_message_pieces_to_memory") as mock_add:
        sqlite_instance.add_message_to_memory(request=Message(message_pieces=sample_conversations))
        assert mock_add.called

        args, kwargs = mock_add.call_args
        assert kwargs["message_pieces"][0].sequence == 1, "Sequence should increment previous conversation by 1"
        assert kwargs["message_pieces"][1].sequence == 1
        assert kwargs["message_pieces"][2].sequence == 1


def test_insert_prompt_memories_inserts_embedding(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    request = Message(message_pieces=[sample_conversations[0]])

    embedding_mock = MagicMock()
    embedding_mock.generate_text_embedding.returns = [0, 1, 2]
    sqlite_instance.enable_embedding(embedding_model=embedding_mock)

    with (
        patch("pyrit.memory.sqlite_memory.SQLiteMemory.add_message_pieces_to_memory"),
        patch("pyrit.memory.sqlite_memory.SQLiteMemory._add_embeddings_to_memory") as mock_embedding,
    ):
        sqlite_instance.add_message_to_memory(request=request)

        assert mock_embedding.called
        assert embedding_mock.generate_text_embedding.called


def test_insert_prompt_memories_not_inserts_embedding(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    request = Message(message_pieces=[sample_conversations[0]])

    embedding_mock = MagicMock()
    embedding_mock.generate_text_embedding.returns = [0, 1, 2]
    sqlite_instance.enable_embedding(embedding_model=embedding_mock)
    sqlite_instance.disable_embedding()

    with (
        patch("pyrit.memory.sqlite_memory.SQLiteMemory.add_message_pieces_to_memory"),
        patch("pyrit.memory.sqlite_memory.SQLiteMemory._add_embeddings_to_memory") as mock_embedding,
    ):
        sqlite_instance.add_message_to_memory(request=request)

        assert mock_embedding.assert_not_called


def test_get_message_pieces_labels(sqlite_instance: MemoryInterface):
    labels = {"operation": "op1", "operator": "name1", "harm_category": "dummy1"}
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 1",
                labels=labels,
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="assistant",
                original_value="Hello 2",
                labels=labels,
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 3",
            )
        ),
    ]

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = sqlite_instance.get_message_pieces(labels=labels)

    assert len(retrieved_entries) == 2  # Two entries should have the specific memory labels
    for retrieved_entry in retrieved_entries:
        assert "operation" in retrieved_entry.labels
        assert "operator" in retrieved_entry.labels
        assert "harm_category" in retrieved_entry.labels


def test_get_message_pieces_labels_falls_back_to_attack_result_labels(sqlite_instance: MemoryInterface):
    """PMEs without labels are returned when a matching AttackResultEntry shares the conversation_id."""
    from pyrit.memory.memory_models import AttackResultEntry
    from pyrit.models import AttackOutcome, AttackResult

    conv_id = str(uuid.uuid4())
    labels = {"operation": "op1", "operator": "name1"}

    # PME with NO labels
    pme = PromptMemoryEntry(
        entry=MessagePiece(
            role="user",
            original_value="Hello from AR",
            conversation_id=conv_id,
        )
    )
    # AttackResultEntry with labels sharing the same conversation_id
    ar = AttackResult(
        conversation_id=conv_id,
        objective="test",
        outcome=AttackOutcome.SUCCESS,
        labels=labels,
    )
    are = AttackResultEntry(entry=ar)

    sqlite_instance._insert_entries(entries=[pme, are])

    retrieved = sqlite_instance.get_message_pieces(labels=labels)
    assert len(retrieved) == 1
    assert retrieved[0].original_value == "Hello from AR"


def test_get_message_pieces_labels_returns_pme_and_ar_label_matches(sqlite_instance: MemoryInterface):
    """Both PMEs with direct labels and PMEs matched via AR labels are returned."""
    from pyrit.memory.memory_models import AttackResultEntry
    from pyrit.models import AttackOutcome, AttackResult

    labels = {"operation": "op1"}

    # PME with direct labels
    pme_direct = PromptMemoryEntry(
        entry=MessagePiece(
            conversation_id=str(uuid4()),
            role="user",
            original_value="Direct label",
            labels=labels,
        )
    )
    # PME without labels, but associated AR has labels
    conv_id = str(uuid.uuid4())
    pme_via_ar = PromptMemoryEntry(
        entry=MessagePiece(
            role="user",
            original_value="Via AR label",
            conversation_id=conv_id,
        )
    )
    ar = AttackResult(
        conversation_id=conv_id,
        objective="test",
        outcome=AttackOutcome.SUCCESS,
        labels=labels,
    )
    are = AttackResultEntry(entry=ar)

    # PME with no labels and no matching AR
    pme_no_match = PromptMemoryEntry(
        entry=MessagePiece(
            conversation_id=str(uuid4()),
            role="user",
            original_value="No match",
        )
    )

    sqlite_instance._insert_entries(entries=[pme_direct, pme_via_ar, are, pme_no_match])

    retrieved = sqlite_instance.get_message_pieces(labels=labels)
    assert len(retrieved) == 2
    original_values = {r.original_value for r in retrieved}
    assert original_values == {"Direct label", "Via AR label"}


def test_get_message_pieces_labels_no_match_when_ar_labels_differ(sqlite_instance: MemoryInterface):
    """PMEs are NOT returned when the AR labels don't match the query."""
    from pyrit.memory.memory_models import AttackResultEntry
    from pyrit.models import AttackOutcome, AttackResult

    conv_id = str(uuid.uuid4())
    pme = PromptMemoryEntry(
        entry=MessagePiece(
            role="user",
            original_value="Unmatched",
            conversation_id=conv_id,
        )
    )
    ar = AttackResult(
        conversation_id=conv_id,
        objective="test",
        outcome=AttackOutcome.SUCCESS,
        labels={"operation": "other_op"},
    )
    are = AttackResultEntry(entry=ar)

    sqlite_instance._insert_entries(entries=[pme, are])

    retrieved = sqlite_instance.get_message_pieces(labels={"operation": "op1"})
    assert len(retrieved) == 0


def test_get_message_pieces_metadata(sqlite_instance: MemoryInterface):
    metadata: dict[str, str | int] = {"key1": "value1", "key2": "value2"}
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 1",
                prompt_metadata=metadata,
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="assistant",
                original_value="Hello 2",
                prompt_metadata={"key2": "value2", "key3": "value3"},
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 3",
            )
        ),
    ]

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = sqlite_instance.get_message_pieces(prompt_metadata={"key2": "value2"})

    assert len(retrieved_entries) == 2  # Two entries should have the specific memory labels
    for retrieved_entry in retrieved_entries:
        assert "key2" in retrieved_entry.prompt_metadata


def test_get_message_pieces_id(sqlite_instance: MemoryInterface):
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 1",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="assistant",
                original_value="Hello 2",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 3",
            )
        ),
    ]

    id_1 = uuid.uuid4()
    id_2 = uuid.uuid4()
    entries[0].id = id_1
    entries[1].id = id_2

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = sqlite_instance.get_message_pieces(prompt_ids=[id_1, id_2])

    assert len(retrieved_entries) == 2
    assert_original_value_in_list("Hello 1", retrieved_entries)
    assert_original_value_in_list("Hello 2", retrieved_entries)


def test_get_message_pieces_attack(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    attack2 = PromptSendingAttack(objective_target=get_mock_target("Target2"))

    pieces = [
        MessagePiece(role="user", original_value="Hello 1", conversation_id="c1", sequence=0),
        MessagePiece(role="assistant", original_value="Hello 2", conversation_id="c2", sequence=0),
        MessagePiece(role="user", original_value="Hello 3", conversation_id="c1", sequence=1),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

    # attack_identifier is no longer stamped on pieces; the deprecated attack_id filter
    # resolves to an attack's main conversation via persisted AttackResults.
    sqlite_instance.add_attack_results_to_memory(
        attack_results=[
            AttackResult(
                conversation_id="c1",
                objective="objective 1",
                atomic_attack_identifier=AtomicAttackIdentifier.build(attack_identifier=attack1.get_identifier()),
            ),
            AttackResult(
                conversation_id="c2",
                objective="objective 2",
                atomic_attack_identifier=AtomicAttackIdentifier.build(attack_identifier=attack2.get_identifier()),
            ),
        ]
    )

    attack1_entries = sqlite_instance.get_message_pieces(attack_id=attack1.get_identifier().hash)

    assert len(attack1_entries) == 2
    assert_original_value_in_list("Hello 1", attack1_entries)
    assert_original_value_in_list("Hello 3", attack1_entries)


def test_get_message_pieces_sent_after(sqlite_instance: MemoryInterface):
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 1",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="assistant",
                original_value="Hello 2",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 3",
            )
        ),
    ]

    entries[0].timestamp = datetime(2022, 12, 25, 15, 30, 0, tzinfo=timezone.utc)
    entries[1].timestamp = datetime(2022, 12, 25, 15, 30, 0, tzinfo=timezone.utc)

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = sqlite_instance.get_message_pieces(sent_after=datetime(2024, 1, 1, tzinfo=timezone.utc))

    assert len(retrieved_entries) == 1
    assert "Hello 3" in retrieved_entries[0].original_value


def test_get_message_pieces_sent_before(sqlite_instance: MemoryInterface):
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 1",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="assistant",
                original_value="Hello 2",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 3",
            )
        ),
    ]

    entries[0].timestamp = datetime(2022, 12, 25, 15, 30, 0, tzinfo=timezone.utc)
    entries[1].timestamp = datetime(2021, 12, 25, 15, 30, 0, tzinfo=timezone.utc)

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = sqlite_instance.get_message_pieces(sent_before=datetime(2024, 1, 1, tzinfo=timezone.utc))

    assert len(retrieved_entries) == 2
    assert_original_value_in_list("Hello 1", retrieved_entries)
    assert_original_value_in_list("Hello 2", retrieved_entries)


def test_get_message_pieces_by_value(sqlite_instance: MemoryInterface):
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 1",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="assistant",
                original_value="Hello 2",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="Hello 3",
            )
        ),
    ]

    sqlite_instance._insert_entries(entries=entries)
    retrieved_entries = sqlite_instance.get_message_pieces(converted_values=["Hello 2", "Hello 3"])

    assert len(retrieved_entries) == 2
    assert_original_value_in_list("Hello 2", retrieved_entries)
    assert_original_value_in_list("Hello 3", retrieved_entries)


def test_get_message_pieces_by_hash(sqlite_instance: MemoryInterface):
    entries = [
        MessagePiece(
            conversation_id=str(uuid4()),
            role="user",
            original_value="Hello 1",
        ),
        MessagePiece(
            conversation_id=str(uuid4()),
            role="assistant",
            original_value="Hello 2",
        ),
        MessagePiece(
            conversation_id=str(uuid4()),
            role="user",
            original_value="Hello 3",
        ),
    ]

    entries[0].converted_value_sha256 = "hash1"
    entries[1].converted_value_sha256 = "hash1"

    sqlite_instance.add_message_pieces_to_memory(message_pieces=entries)
    retrieved_entries = sqlite_instance.get_message_pieces(converted_value_sha256=["hash1"])

    assert len(retrieved_entries) == 2
    assert_original_value_in_list("Hello 1", retrieved_entries)
    assert_original_value_in_list("Hello 2", retrieved_entries)


def test_get_message_pieces_with_non_matching_memory_labels(sqlite_instance: MemoryInterface):
    attack = PromptSendingAttack(objective_target=get_mock_target())
    labels = {"operation": "op1", "operator": "name1", "harm_category": "dummy1"}
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id="123",
                role="user",
                original_value="Hello 1",
                labels=labels,
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id="456",
                role="assistant",
                original_value="Hello 2",
                labels=labels,
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id="789",
                role="user",
                original_value="Hello 3",
                converted_value="Hello 1",
            )
        ),
    ]

    sqlite_instance._insert_entries(entries=entries)
    labels = {"nonexistent_key": "nonexiststent_value"}
    retrieved_entries = sqlite_instance.get_message_pieces(labels=labels)

    assert len(retrieved_entries) == 0  # zero entries found since invalid memory labels passed


def test_get_message_pieces_sorts(
    sqlite_instance: MemoryInterface, sample_conversations: MutableSequence[MessagePiece]
):
    conversation_id = sample_conversations[0].conversation_id

    # This new conversation piece should be grouped with other messages in the conversation
    sample_conversations.append(
        MessagePiece(
            role="user",
            original_value="original prompt text",
            conversation_id=conversation_id,
        )
    )

    sqlite_instance.add_message_pieces_to_memory(message_pieces=sample_conversations)

    response = sqlite_instance.get_message_pieces()

    current_value = response[0].conversation_id
    for obj in response[1:]:
        new_value = obj.conversation_id
        if new_value != current_value and any(
            o.conversation_id == current_value for o in response[response.index(obj) :]
        ):
            raise AssertionError("Conversation IDs are not grouped together")


def test_message_piece_scores_duplicate_piece(sqlite_instance: MemoryInterface):
    """Scores for duplicated pieces are returned via get_prompt_scores."""
    original_id = uuid4()
    duplicate_id = uuid4()

    pieces = [
        MessagePiece(
            conversation_id=str(uuid4()),
            id=original_id,
            role="assistant",
            original_value="prompt text",
        ),
        MessagePiece(
            conversation_id=str(uuid4()),
            id=duplicate_id,
            role="assistant",
            original_value="prompt text",
            original_prompt_id=original_id,
        ),
    ]

    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

    score = Score(
        score_value=str(0.8),
        score_value_description="Sample description",
        score_type="float_scale",
        score_category=["Sample category"],
        score_rationale="Sample rationale",
        score_metadata={"sample": "metadata"},
        message_piece_id=original_id,
        scorer_class_identifier=_test_scorer_id(),
    )
    sqlite_instance.add_scores_to_memory(scores=[score])

    # Both the original and the duplicate piece resolve back to the same score
    # via get_prompt_scores, which queries ScoreEntry by original_prompt_id.
    scores_for_original = sqlite_instance.get_prompt_scores(prompt_ids=[str(original_id)])
    scores_for_duplicate = sqlite_instance.get_prompt_scores(prompt_ids=[str(duplicate_id)])

    assert len(scores_for_original) == 1
    assert scores_for_original[0].score_value == "0.8"
    assert len(scores_for_duplicate) == 1
    assert scores_for_duplicate[0].score_value == "0.8"


async def test_message_piece_hash_stored_and_retrieved(sqlite_instance: MemoryInterface):
    entries = [
        MessagePiece(
            conversation_id=str(uuid4()),
            role="user",
            original_value="Hello 1",
        ),
        MessagePiece(
            conversation_id=str(uuid4()),
            role="assistant",
            original_value="Hello 2",
        ),
    ]

    for entry in entries:
        await entry.set_sha256_values_async()

    sqlite_instance.add_message_pieces_to_memory(message_pieces=entries)
    retrieved_entries = sqlite_instance.get_message_pieces()

    assert len(retrieved_entries) == 2
    for prompt in retrieved_entries:
        assert prompt.converted_value_sha256
        assert prompt.original_value_sha256


async def test_seed_prompt_hash_stored_and_retrieved(sqlite_instance: MemoryInterface):
    """Test that seed prompt hash values are properly stored and retrieved."""
    # Create a seed prompt
    seed_prompt = SeedPrompt(
        value="Test seed prompt",
        data_type="text",
        dataset_name="test_dataset",
        added_by="test_user",
    )

    # Add to memory
    await sqlite_instance.add_seeds_to_memory_async(seeds=[seed_prompt])

    # Retrieve and verify hash
    assert seed_prompt.value_sha256 is not None, "SHA256 should not be None"
    retrieved_prompts = sqlite_instance.get_seeds(value_sha256=[seed_prompt.value_sha256])
    assert len(retrieved_prompts) == 1
    assert retrieved_prompts[0].value_sha256 == seed_prompt.value_sha256


def test_get_request_from_response_success(sqlite_instance: MemoryInterface):
    """Test that get_request_from_response successfully retrieves the request that produced a response."""
    conversation_id = str(uuid4())

    # Create a conversation with user request followed by assistant response
    pieces = [
        MessagePiece(
            role="user",
            original_value="What is the weather?",
            converted_value="What is the weather?",
            conversation_id=conversation_id,
            sequence=0,
        ),
        MessagePiece(
            role="assistant",
            original_value="It's sunny today.",
            converted_value="It's sunny today.",
            conversation_id=conversation_id,
            sequence=1,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

    # Get the conversation and extract the response
    conversation = sqlite_instance.get_conversation_messages(conversation_id=conversation_id)
    response = conversation[1]

    # Retrieve the request that produced this response
    request = sqlite_instance.get_request_from_response(response=response)

    assert request.api_role == "user"
    assert request.sequence == 0
    assert request.get_value() == "What is the weather?"
    assert request.conversation_id == conversation_id


def test_get_conversation_is_deprecated_and_delegates_to_messages(sqlite_instance: MemoryInterface):
    """get_conversation warns and returns the same result as get_conversation_messages."""
    conversation_id = str(uuid4())
    pieces = [
        MessagePiece(
            role="user",
            original_value="Hello",
            converted_value="Hello",
            conversation_id=conversation_id,
            sequence=0,
        ),
        MessagePiece(
            role="assistant",
            original_value="Hi there",
            converted_value="Hi there",
            conversation_id=conversation_id,
            sequence=1,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

    with pytest.warns(DeprecationWarning, match="get_conversation_messages"):
        deprecated_result = sqlite_instance.get_conversation(conversation_id=conversation_id)

    expected = sqlite_instance.get_conversation_messages(conversation_id=conversation_id)
    assert [m.get_value() for m in deprecated_result] == [m.get_value() for m in expected]
    assert len(deprecated_result) == 2


def test_get_request_from_response_multi_turn_conversation(sqlite_instance: MemoryInterface):
    """Test get_request_from_response in a multi-turn conversation."""
    conversation_id = str(uuid4())

    # Create a multi-turn conversation
    pieces = [
        MessagePiece(
            role="user",
            original_value="First question",
            converted_value="First question",
            conversation_id=conversation_id,
            sequence=0,
        ),
        MessagePiece(
            role="assistant",
            original_value="First answer",
            converted_value="First answer",
            conversation_id=conversation_id,
            sequence=1,
        ),
        MessagePiece(
            role="user",
            original_value="Second question",
            converted_value="Second question",
            conversation_id=conversation_id,
            sequence=2,
        ),
        MessagePiece(
            role="assistant",
            original_value="Second answer",
            converted_value="Second answer",
            conversation_id=conversation_id,
            sequence=3,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

    conversation = sqlite_instance.get_conversation_messages(conversation_id=conversation_id)

    # Test getting request for the second response
    second_response = conversation[3]
    second_request = sqlite_instance.get_request_from_response(response=second_response)

    assert second_request.api_role == "user"
    assert second_request.sequence == 2
    assert second_request.get_value() == "Second question"


def test_get_request_from_response_raises_error_for_non_assistant_role(sqlite_instance: MemoryInterface):
    """Test that get_request_from_response raises ValueError when given a non-assistant role."""
    conversation_id = str(uuid4())

    pieces = [
        MessagePiece(
            role="user",
            original_value="Test message",
            converted_value="Test message",
            conversation_id=conversation_id,
            sequence=0,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

    conversation = sqlite_instance.get_conversation_messages(conversation_id=conversation_id)
    user_message = conversation[0]

    with pytest.raises(ValueError, match="The provided request is not a response \\(role must be 'assistant'\\)."):
        sqlite_instance.get_request_from_response(response=user_message)


def test_get_request_from_response_raises_error_for_sequence_less_than_one(sqlite_instance: MemoryInterface):
    """Test that get_request_from_response raises ValueError when sequence < 1."""
    conversation_id = str(uuid4())

    # Create a response with sequence 0 (which shouldn't have a preceding request)
    pieces = [
        MessagePiece(
            role="assistant",
            original_value="Response without request",
            converted_value="Response without request",
            conversation_id=conversation_id,
            sequence=0,
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)

    conversation = sqlite_instance.get_conversation_messages(conversation_id=conversation_id)
    response_without_request = conversation[0]

    with pytest.raises(ValueError, match="The provided request does not have a preceding request \\(sequence < 1\\)."):
        sqlite_instance.get_request_from_response(response=response_without_request)


def test_get_message_pieces_by_attack_identifier_filter(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=get_mock_target())

    # IdentifierType.ATTACK is no longer stamped on message pieces, so the piece-level
    # identifier filter rejects it. Attack filtering now goes through get_attack_results
    # or the deprecated attack_id parameter.
    with pytest.raises(ValueError, match="does not support identifier type"):
        sqlite_instance.get_message_pieces(
            identifier_filters=[
                IdentifierFilter(
                    identifier_type=IdentifierType.ATTACK,
                    property_path="$.hash",
                    value=attack1.get_identifier().hash,
                    partial_match=False,
                )
            ],
        )


def test_get_message_pieces_by_target_identifier_filter(sqlite_instance: MemoryInterface):
    target_id_1 = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://api.openai.com", "model_name": "gpt-4"},
    )
    target_id_2 = ComponentIdentifier(
        class_name="AzureChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://azure.com", "model_name": "gpt-3.5"},
    )

    sqlite_instance.add_conversation_to_memory(
        conversation=Conversation(conversation_id="conv-openai", target_identifier=target_id_1)
    )
    sqlite_instance.add_message_pieces_to_memory(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Hello OpenAI",
                conversation_id="conv-openai",
            ),
        ],
    )
    sqlite_instance.add_conversation_to_memory(
        conversation=Conversation(conversation_id="conv-azure", target_identifier=target_id_2)
    )
    sqlite_instance.add_message_pieces_to_memory(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Hello Azure",
                conversation_id="conv-azure",
            ),
        ],
    )

    # Filter by target hash
    results = sqlite_instance.get_message_pieces(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.TARGET,
                property_path="$.hash",
                value=target_id_1.hash,
                partial_match=False,
            )
        ],
    )
    assert len(results) == 1
    assert results[0].original_value == "Hello OpenAI"

    # Filter by endpoint partial match
    results = sqlite_instance.get_message_pieces(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.TARGET,
                property_path="$.endpoint",
                value="openai",
                partial_match=True,
            )
        ],
    )
    assert len(results) == 1
    assert results[0].original_value == "Hello OpenAI"

    # No match
    results = sqlite_instance.get_message_pieces(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.TARGET,
                property_path="$.hash",
                value="nonexistent",
                partial_match=False,
            )
        ],
    )
    assert len(results) == 0


def test_get_message_pieces_by_converter_identifier_filter_with_array_element_path(sqlite_instance: MemoryInterface):
    converter_a = ComponentIdentifier(
        class_name="Base64Converter",
        class_module="pyrit.prompt_converter",
    )
    converter_b = ComponentIdentifier(
        class_name="ROT13Converter",
        class_module="pyrit.prompt_converter",
    )

    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="With Base64",
                converter_identifiers=[converter_a],
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="With both converters",
                converter_identifiers=[converter_a, converter_b],
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                conversation_id=str(uuid4()),
                role="user",
                original_value="No converters",
            )
        ),
    ]

    sqlite_instance._insert_entries(entries=entries)

    # Filter by converter class_name using array_element_path (array element matching)
    results = sqlite_instance.get_message_pieces(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.CONVERTER,
                property_path="$",
                array_element_path="$.class_name",
                value="Base64Converter",
            )
        ],
    )
    assert len(results) == 2
    original_values = {r.original_value for r in results}
    assert original_values == {"With Base64", "With both converters"}

    # Filter by ROT13Converter — only the entry with both converters
    results = sqlite_instance.get_message_pieces(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.CONVERTER,
                property_path="$",
                array_element_path="$.class_name",
                value="ROT13Converter",
            )
        ],
    )
    assert len(results) == 1
    assert results[0].original_value == "With both converters"

    # No match
    results = sqlite_instance.get_message_pieces(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.CONVERTER,
                property_path="$",
                array_element_path="$.class_name",
                value="NonexistentConverter",
            )
        ],
    )
    assert len(results) == 0
