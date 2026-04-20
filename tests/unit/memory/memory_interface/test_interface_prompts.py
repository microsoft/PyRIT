# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import uuid
from datetime import datetime
from typing import MutableSequence, Sequence
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import MemoryInterface, PromptMemoryEntry
from pyrit.models import (
    Message,
    MessagePiece,
    Score,
    SeedPrompt,
)


def _test_scorer_id(name: str = "TestScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="tests.unit.memory",
        class_description="",
        identifier_type="instance",
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
        c._role = sample_conversations[0]._role
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
            id=uuid1,
            role="user",
            original_value="Test prompt 1",
            converted_value="Test prompt 1",
        ),
        MessagePiece(
            id=uuid2,
            role="assistant",
            original_value="Test prompt 2",
            converted_value="Test prompt 2",
        ),
        MessagePiece(
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


def test_duplicate_memory(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    attack2 = PromptSendingAttack(objective_target=MagicMock())
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
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id_1,
            sequence=1,
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id_3,
            attack_identifier=attack2.get_identifier(),
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id_2,
            sequence=0,
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id_2,
            sequence=1,
            attack_identifier=attack1.get_identifier(),
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
    # Attack IDs are preserved (not changed) when duplicating
    assert len([p for p in all_pieces if p.attack_identifier["id"] == attack1.get_identifier()["id"]]) == 8
    assert len([p for p in all_pieces if p.attack_identifier["id"] == attack2.get_identifier()["id"]]) == 1
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
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    memory_labels = {"sample": "label"}
    pieces = [
        MessagePiece(
            id=prompt_id_1,
            role="assistant",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id,
            sequence=0,
            attack_identifier=attack1.get_identifier(),
            labels=memory_labels,
        ),
        MessagePiece(
            id=prompt_id_2,
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id,
            sequence=0,
            attack_identifier=attack1.get_identifier(),
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
    # Attack ID is preserved, so both original and duplicated pieces have the same attack ID
    assert len(sqlite_instance.get_prompt_scores(attack_id=attack1.get_identifier()["id"])) == 2

    # The duplicate prompts ids should not have scores so only two scores are returned
    assert len(sqlite_instance.get_prompt_scores(prompt_ids=[str(prompt_id_1), str(prompt_id_2)] + new_pieces_ids)) == 2


def test_duplicate_conversation_excluding_last_turn(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    attack2 = PromptSendingAttack(objective_target=MagicMock())
    conversation_id_1 = "11111"
    conversation_id_2 = "22222"
    pieces = [
        MessagePiece(
            role="user",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=0,
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=1,
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            sequence=2,
            conversation_id=conversation_id_1,
            attack_identifier=attack2.get_identifier(),
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id_2,
            sequence=2,
            attack_identifier=attack2.get_identifier(),
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id_2,
            sequence=3,
            attack_identifier=attack1.get_identifier(),
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
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    memory_labels = {"sample": "label"}
    pieces = [
        MessagePiece(
            id=prompt_id_1,
            role="user",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id,
            sequence=0,
            attack_identifier=attack1.get_identifier(),
            labels=memory_labels,
        ),
        MessagePiece(
            id=prompt_id_2,
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id,
            sequence=1,
            attack_identifier=attack1.get_identifier(),
            labels=memory_labels,
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="That's good.",
            conversation_id=conversation_id,
            sequence=2,
            attack_identifier=attack1.get_identifier(),
            labels=memory_labels,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="Thanks.",
            conversation_id=conversation_id,
            sequence=3,
            attack_identifier=attack1.get_identifier(),
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
    # Attack ID is preserved
    assert len(sqlite_instance.get_prompt_scores(attack_id=attack1.get_identifier()["id"])) == 2
    # The duplicate prompts ids should not have scores so only two scores are returned
    assert len(sqlite_instance.get_prompt_scores(prompt_ids=[str(prompt_id_1), str(prompt_id_2)] + new_pieces_ids)) == 2


def test_duplicate_conversation_excluding_last_turn_same_attack(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    conversation_id_1 = "11111"
    pieces = [
        MessagePiece(
            role="user",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=0,
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=1,
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=2,
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            conversation_id=conversation_id_1,
            sequence=3,
            attack_identifier=attack1.get_identifier(),
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


def test_duplicate_memory_preserves_attack_id(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    conversation_id = "11111"
    pieces = [
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id,
            sequence=0,
            attack_identifier=attack1.get_identifier(),
        ),
    ]
    sqlite_instance.add_message_pieces_to_memory(message_pieces=pieces)
    assert len(sqlite_instance.get_message_pieces()) == 1

    # Duplicating preserves the attack ID
    new_conversation_id = sqlite_instance.duplicate_conversation(
        conversation_id=conversation_id,
    )

    # Verify duplication succeeded
    all_pieces = sqlite_instance.get_message_pieces()
    assert len(all_pieces) == 2
    assert new_conversation_id != conversation_id

    # Both pieces should have the same attack ID
    attack_ids = {p.attack_identifier["id"] for p in all_pieces}
    assert len(attack_ids) == 1
    assert attack1.get_identifier()["id"] in attack_ids


def test_duplicate_conversation_creates_new_ids(sqlite_instance: MemoryInterface):
    """Test that duplicated conversation has new piece IDs."""
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    conversation_id = "test-conv-123"
    original_piece = MessagePiece(
        role="user",
        original_value="original prompt text",
        converted_value="Hello",
        conversation_id=conversation_id,
        sequence=1,
        attack_identifier=attack1.get_identifier(),
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
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    conversation_id = "test-conv-456"
    original_piece = MessagePiece(
        role="user",
        original_value="traceable prompt",
        conversation_id=conversation_id,
        sequence=1,
        attack_identifier=attack1.get_identifier(),
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
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    conversation_id = "multi-piece-conv"

    pieces = [
        MessagePiece(
            role="user",
            original_value="user message 1",
            conversation_id=conversation_id,
            sequence=1,
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="assistant",
            original_value="assistant response 1",
            conversation_id=conversation_id,
            sequence=2,
            attack_identifier=attack1.get_identifier(),
        ),
        MessagePiece(
            role="user",
            original_value="user message 2",
            conversation_id=conversation_id,
            sequence=3,
            attack_identifier=attack1.get_identifier(),
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
        sorted(original_pieces, key=lambda p: p.sequence), sorted(new_pieces, key=lambda p: p.sequence)
    ):
        assert orig.sequence == new.sequence
        assert orig.api_role == new.api_role
        assert orig.original_value == new.original_value


def test_add_message_pieces_to_memory_calls_validate(sqlite_instance: MemoryInterface):
    message = MagicMock(Message)
    message.message_pieces = [MagicMock(MessagePiece)]
    with (
        patch("pyrit.memory.sqlite_memory.SQLiteMemory.add_message_pieces_to_memory"),
        patch("pyrit.memory.memory_interface.MemoryInterface._update_sequence"),
    ):
        sqlite_instance.add_message_to_memory(request=message)
    assert message.validate.called


def test_add_message_pieces_to_memory_updates_sequence(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    for conversation in sample_conversations:
        conversation.conversation_id = sample_conversations[0].conversation_id
        conversation._role = sample_conversations[0]._role
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
        conversation._role = sample_conversations[0]._role
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
    labels = {"op_name": "op1", "user_name": "name1", "harm_category": "dummy1"}
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 1",
                labels=labels,
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="assistant",
                original_value="Hello 2",
                labels=labels,
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 3",
            )
        ),
    ]

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = sqlite_instance.get_message_pieces(labels=labels)

    assert len(retrieved_entries) == 2  # Two entries should have the specific memory labels
    for retrieved_entry in retrieved_entries:
        assert "op_name" in retrieved_entry.labels
        assert "user_name" in retrieved_entry.labels
        assert "harm_category" in retrieved_entry.labels


def test_get_message_pieces_metadata(sqlite_instance: MemoryInterface):
    metadata: dict[str, str | int] = {"key1": "value1", "key2": "value2"}
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 1",
                prompt_metadata=metadata,
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="assistant",
                original_value="Hello 2",
                prompt_metadata={"key2": "value2", "key3": "value3"},
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
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
                role="user",
                original_value="Hello 1",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="assistant",
                original_value="Hello 2",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
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
    attack1 = PromptSendingAttack(objective_target=MagicMock())
    attack2 = PromptSendingAttack(objective_target=MagicMock())

    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 1",
                attack_identifier=attack1.get_identifier(),
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="assistant",
                original_value="Hello 2",
                attack_identifier=attack2.get_identifier(),
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 3",
                attack_identifier=attack1.get_identifier(),
            )
        ),
    ]

    sqlite_instance._insert_entries(entries=entries)

    attack1_entries = sqlite_instance.get_message_pieces(attack_id=attack1.get_identifier()["id"])

    assert len(attack1_entries) == 2
    assert_original_value_in_list("Hello 1", attack1_entries)
    assert_original_value_in_list("Hello 3", attack1_entries)


def test_get_message_pieces_sent_after(sqlite_instance: MemoryInterface):
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 1",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="assistant",
                original_value="Hello 2",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 3",
            )
        ),
    ]

    entries[0].timestamp = datetime(2022, 12, 25, 15, 30, 0)
    entries[1].timestamp = datetime(2022, 12, 25, 15, 30, 0)

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = sqlite_instance.get_message_pieces(sent_after=datetime(2024, 1, 1))

    assert len(retrieved_entries) == 1
    assert "Hello 3" in retrieved_entries[0].original_value


def test_get_message_pieces_sent_before(sqlite_instance: MemoryInterface):
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 1",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="assistant",
                original_value="Hello 2",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 3",
            )
        ),
    ]

    entries[0].timestamp = datetime(2022, 12, 25, 15, 30, 0)
    entries[1].timestamp = datetime(2021, 12, 25, 15, 30, 0)

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = sqlite_instance.get_message_pieces(sent_before=datetime(2024, 1, 1))

    assert len(retrieved_entries) == 2
    assert_original_value_in_list("Hello 1", retrieved_entries)
    assert_original_value_in_list("Hello 2", retrieved_entries)


def test_get_message_pieces_by_value(sqlite_instance: MemoryInterface):
    entries = [
        PromptMemoryEntry(
            entry=MessagePiece(
                role="user",
                original_value="Hello 1",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
                role="assistant",
                original_value="Hello 2",
            )
        ),
        PromptMemoryEntry(
            entry=MessagePiece(
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
            role="user",
            original_value="Hello 1",
        ),
        MessagePiece(
            role="assistant",
            original_value="Hello 2",
        ),
        MessagePiece(
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
    attack = PromptSendingAttack(objective_target=MagicMock())
    labels = {"op_name": "op1", "user_name": "name1", "harm_category": "dummy1"}
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
                attack_identifier=attack.get_identifier(),
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
        if new_value != current_value:
            if any(o.conversation_id == current_value for o in response[response.index(obj) :]):
                assert False, "Conversation IDs are not grouped together"


def test_message_piece_scores_duplicate_piece(sqlite_instance: MemoryInterface):
    original_id = uuid4()
    duplicate_id = uuid4()

    pieces = [
        MessagePiece(
            id=original_id,
            role="assistant",
            original_value="prompt text",
        ),
        MessagePiece(
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

    retrieved_pieces = sqlite_instance.get_message_pieces()

    assert len(retrieved_pieces[0].scores) == 1
    assert retrieved_pieces[0].scores[0].score_value == "0.8"

    # Check that the duplicate piece has the same score as the original
    assert len(retrieved_pieces[1].scores) == 1
    assert retrieved_pieces[1].scores[0].score_value == "0.8"


@pytest.mark.asyncio
async def test_message_piece_hash_stored_and_retrieved(sqlite_instance: MemoryInterface):
    entries = [
        MessagePiece(
            role="user",
            original_value="Hello 1",
        ),
        MessagePiece(
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


@pytest.mark.asyncio
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
    conversation = sqlite_instance.get_conversation(conversation_id=conversation_id)
    response = conversation[1]

    # Retrieve the request that produced this response
    request = sqlite_instance.get_request_from_response(response=response)

    assert request.api_role == "user"
    assert request.sequence == 0
    assert request.get_value() == "What is the weather?"
    assert request.conversation_id == conversation_id


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

    conversation = sqlite_instance.get_conversation(conversation_id=conversation_id)

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

    conversation = sqlite_instance.get_conversation(conversation_id=conversation_id)
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

    conversation = sqlite_instance.get_conversation(conversation_id=conversation_id)
    response_without_request = conversation[0]

    with pytest.raises(ValueError, match="The provided request does not have a preceding request \\(sequence < 1\\)."):
        sqlite_instance.get_request_from_response(response=response_without_request)
