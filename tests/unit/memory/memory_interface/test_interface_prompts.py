# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import json
import uuid
from collections.abc import MutableSequence, Sequence
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from unit.mocks import get_mock_target

from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.memory import MemoryInterface, PromptMemoryEntry
from pyrit.memory.memory_models import (
    ConverterIdentifierEntry,
    PromptConverterIdentifierEntry,
    TargetIdentifierEntry,
)
from pyrit.memory.storage.serializers import set_message_piece_sha256_async
from pyrit.models import (
    ChatMessageRole,
    ComponentIdentifier,
    Conversation,
    ConverterIdentifier,
    IdentifierFilter,
    IdentifierType,
    Message,
    MessagePiece,
    PromptDataType,
    RequestTraceContext,
    Score,
    SeedPrompt,
    TargetIdentifier,
)
from pyrit.prompt_target.common.utils import set_response_metadata


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


async def test_conversation_memory_empty_by_default(sqlite_instance: MemoryInterface):
    expected_count = 0
    c = await sqlite_instance.get_message_pieces_async()
    assert len(c) == expected_count


@pytest.mark.parametrize("num_conversations", [1, 2, 3])
async def test_add_message_pieces_to_memory(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece], num_conversations: int
):
    for c in sample_conversations[:num_conversations]:
        c.conversation_id = sample_conversations[0].conversation_id
        c.role = sample_conversations[0].role
        c.sequence = 0

    message = Message(message_pieces=sample_conversations[:num_conversations])

    (await sqlite_instance.add_message_to_memory_async(request=message))
    assert len(await sqlite_instance.get_message_pieces_async()) == num_conversations


async def test_add_message_pieces_preserves_same_sequence_order(sqlite_instance: MemoryInterface):
    conversation_id = str(uuid4())
    timestamp = datetime.now(tz=UTC)
    pieces = [
        MessagePiece(
            id="00000000-0000-4000-8000-0000000000ff",
            role="user",
            original_value="first",
            conversation_id=conversation_id,
            sequence=0,
            timestamp=timestamp,
        ),
        MessagePiece(
            id="00000000-0000-4000-8000-000000000001",
            role="user",
            original_value="second",
            conversation_id=conversation_id,
            sequence=0,
            timestamp=timestamp,
        ),
    ]

    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

    persisted_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)
    assert [piece.original_value for piece in persisted_pieces] == ["first", "second"]
    assert persisted_pieces[0].timestamp < persisted_pieces[1].timestamp
    assert pieces[0].timestamp == pieces[1].timestamp


async def test_add_message_pieces_persists_converter_identifier_graph(sqlite_instance: MemoryInterface):
    target = TargetIdentifier(
        class_name="ConverterTarget",
        class_module="tests.unit.memory",
        model_name="converter-model",
    )
    nested = ConverterIdentifier(
        class_name="NestedConverter",
        class_module="tests.unit.memory",
        supported_input_types=["text"],
        supported_output_types=["text"],
        converter_target=target,
    )
    converter = ConverterIdentifier(
        class_name="CompositeConverter",
        class_module="tests.unit.memory",
        supported_input_types=["text"],
        supported_output_types=["text"],
        sub_converter=nested,
    )
    pieces = [
        MessagePiece(
            conversation_id=str(uuid4()),
            role="user",
            original_value=f"Prompt {index}",
            converter_identifiers=[converter, nested],
        )
        for index in range(2)
    ]

    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

    converter_rows = sqlite_instance._query_entries(ConverterIdentifierEntry)
    link_rows = sqlite_instance._query_entries(PromptConverterIdentifierEntry)
    assert {row.hash for row in converter_rows} == {converter.hash, nested.hash}
    assert next(row for row in converter_rows if row.hash == converter.hash).sub_converter_hash == nested.hash
    assert next(row for row in converter_rows if row.hash == nested.hash).converter_target_hash == target.hash
    assert len(sqlite_instance._query_entries(TargetIdentifierEntry)) == 1
    assert len(link_rows) == 4
    assert {(row.position, row.converter_identifier_hash) for row in link_rows} == {
        (0, converter.hash),
        (1, nested.hash),
    }


async def test_get_message_pieces_uuid_and_string_ids(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

    uuid_results = await sqlite_instance.get_message_pieces_async(prompt_ids=[uuid1, uuid2])
    assert len(uuid_results) == 2
    assert {str(uuid1), str(uuid2)} == {str(piece.id) for piece in uuid_results}

    str_results = await sqlite_instance.get_message_pieces_async(prompt_ids=[str(uuid1), str(uuid2)])
    assert len(str_results) == 2
    assert {str(uuid1), str(uuid2)} == {str(piece.id) for piece in str_results}

    mixed_types: Sequence[str | uuid.UUID] = [uuid1, str(uuid2)]
    mixed_results = await sqlite_instance.get_message_pieces_async(prompt_ids=mixed_types)
    assert len(mixed_results) == 2
    assert {str(uuid1), str(uuid2)} == {str(piece.id) for piece in mixed_results}

    single_uuid_result = await sqlite_instance.get_message_pieces_async(prompt_ids=[uuid3])
    assert len(single_uuid_result) == 1
    assert str(single_uuid_result[0].id) == str(uuid3)

    single_str_result = await sqlite_instance.get_message_pieces_async(prompt_ids=[str(uuid3)])
    assert len(single_str_result) == 1
    assert str(single_str_result[0].id) == str(uuid3)


async def test_get_message_pieces_empty_prompt_ids_returns_empty(sqlite_instance: MemoryInterface):
    piece = MessagePiece(
        conversation_id=str(uuid4()),
        id=uuid.uuid4(),
        role="user",
        original_value="Test prompt",
        converted_value="Test prompt",
    )
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[piece]))

    assert (await sqlite_instance.get_message_pieces_async(prompt_ids=[])) == []


async def test_duplicate_memory(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))
    assert len(await sqlite_instance.get_message_pieces_async()) == 5
    new_conversation_id1 = await sqlite_instance.duplicate_conversation_async(
        conversation_id=conversation_id_1,
    )
    new_conversation_id2 = await sqlite_instance.duplicate_conversation_async(
        conversation_id=conversation_id_2,
    )
    all_pieces = await sqlite_instance.get_message_pieces_async()
    assert len(all_pieces) == 9
    assert len([p for p in all_pieces if p.conversation_id == conversation_id_1]) == 2
    assert len([p for p in all_pieces if p.conversation_id == conversation_id_2]) == 2
    assert len([p for p in all_pieces if p.conversation_id == conversation_id_3]) == 1
    assert len([p for p in all_pieces if p.conversation_id == new_conversation_id1]) == 2
    assert len([p for p in all_pieces if p.conversation_id == new_conversation_id2]) == 2


# Ensure that the score entries are not duplicated when a conversation is duplicated
async def test_duplicate_conversation_pieces_not_score(sqlite_instance: MemoryInterface):
    conversation_id = str(uuid4())
    prompt_id_1 = uuid4()
    prompt_id_2 = uuid4()
    pieces = [
        MessagePiece(
            id=prompt_id_1,
            role="assistant",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id,
            sequence=0,
        ),
        MessagePiece(
            id=prompt_id_2,
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id,
            sequence=0,
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))
    (await sqlite_instance.add_scores_to_memory_async(scores=scores))
    new_conversation_id = await sqlite_instance.duplicate_conversation_async(
        conversation_id=conversation_id,
    )
    new_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=new_conversation_id)
    new_pieces_ids = [str(p.id) for p in new_pieces]
    assert len(new_pieces) == 2
    original_ids = {piece.original_prompt_id for piece in new_pieces}
    assert original_ids == {prompt_id_1, prompt_id_2}

    for piece in new_pieces:
        assert piece.id not in (prompt_id_1, prompt_id_2)

    # The duplicate prompts ids should not have scores so only two scores are returned
    assert (
        len(
            await sqlite_instance.get_prompt_scores_async(
                prompt_ids=[str(prompt_id_1), str(prompt_id_2)] + new_pieces_ids
            )
        )
        == 2
    )


async def test_duplicate_conversation_excluding_last_turn(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))
    assert len(await sqlite_instance.get_message_pieces_async()) == 5

    new_conversation_id1 = await sqlite_instance.duplicate_conversation_excluding_last_turn_async(
        conversation_id=conversation_id_1,
    )

    all_memory = await sqlite_instance.get_message_pieces_async()
    assert len(all_memory) == 7

    duplicate_conversation = await sqlite_instance.get_message_pieces_async(conversation_id=new_conversation_id1)
    assert len(duplicate_conversation) == 2

    for piece in duplicate_conversation:
        assert piece.sequence < 2


def _backtrack_history_pieces(
    *, history: Sequence[str], conversation_id: str, sequence_step: int
) -> list[MessagePiece]:
    roles: dict[str, ChatMessageRole] = {"s": "system", "u": "user", "a": "assistant", "t": "assistant", "r": "tool"}
    pending_calls: list[str] = []
    pieces: list[MessagePiece] = []
    for sequence, message_roles in enumerate(history):
        for role in message_roles:
            data_type: PromptDataType = "text"
            value = f"message {len(pieces)}"
            if role == "t":
                call_id = f"call_{len(pieces)}"
                pending_calls.append(call_id)
                data_type = "function_call"
                value = json.dumps({"type": data_type, "call_id": call_id, "name": "lookup", "arguments": "{}"})
            elif role == "r":
                data_type = "function_call_output"
                value = json.dumps({"type": data_type, "call_id": pending_calls.pop(0), "output": "sunny"})
            pieces.append(
                MessagePiece(
                    role=roles[role],
                    original_value=value,
                    original_value_data_type=data_type,
                    conversation_id=conversation_id,
                    sequence=sequence * sequence_step,
                )
            )
    return pieces


# s: system, u: user, a: assistant, t: function call, r: tool result.
# Each element is one message; repeated letters represent pieces sharing its sequence.
@pytest.mark.parametrize("sequence_step", [1, 3], ids=["consecutive", "gapped"])
@pytest.mark.parametrize(
    "history, expected",
    [
        ("", ""),
        ("s", ""),
        ("u", ""),
        ("ua", ""),
        ("sua", "s"),
        ("uaua", "ua"),
        ("suauaua", "suaua"),
        ("suauau", "suaua"),
        ("sut", "s"),
        ("sutr", "s"),
        ("sutra", "s"),
        ("utra", ""),
        ("suautra", "sua"),
        ("sutraua", "sutra"),
        ("utrautra", "utra"),
        ("sutrautra", "sutra"),
        ("sutratra", "s"),
        ("suautratra", "sua"),
        ("suttrra", "s"),
        ("suauttrra", "sua"),
        ("sutrautr", "sutra"),
        ("sutrau", "sutra"),
        ("suas", "sua"),
        ("suaa", "s"),
        ("aa", ""),
        ("ssua", "ss"),
        pytest.param(("s", "uu", "tt", "rr", "aa"), ("s",), id="first-multipiece-turn"),
        pytest.param(
            ("s", "uu", "tt", "rr", "aa", "uu", "tt", "rr", "aa"),
            ("s", "uu", "tt", "rr", "aa"),
            id="retain-multipiece-tool-turn",
        ),
        pytest.param(
            ("s", "uu", "tt", "rr", "aa", "uu"),
            ("s", "uu", "tt", "rr", "aa"),
            id="multipiece-half-turn",
        ),
        pytest.param(("s", "uu", "tt"), ("s",), id="multipiece-pending-calls"),
    ],
)
async def test_duplicate_conversation_excluding_last_turn_role_sequences_async(
    *,
    sqlite_instance: MemoryInterface,
    history: Sequence[str],
    expected: Sequence[str],
    sequence_step: int,
) -> None:
    conversation_id = str(uuid4())
    pieces = _backtrack_history_pieces(history=history, conversation_id=conversation_id, sequence_step=sequence_step)
    if pieces:
        await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces)
    source = await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)

    new_conversation_id = await sqlite_instance.duplicate_conversation_excluding_last_turn_async(
        conversation_id=conversation_id,
    )

    duplicate_conversation = await sqlite_instance.get_message_pieces_async(conversation_id=new_conversation_id)
    retained = source[: sum(len(message_roles) for message_roles in expected)]
    assert list(history[: len(expected)]) == list(expected)
    assert new_conversation_id != conversation_id
    assert all(piece.conversation_id == new_conversation_id for piece in duplicate_conversation)
    assert [piece.role for piece in duplicate_conversation] == [piece.role for piece in retained]
    assert [piece.original_value for piece in duplicate_conversation] == [piece.original_value for piece in retained]
    assert [piece.original_value_data_type for piece in duplicate_conversation] == [
        piece.original_value_data_type for piece in retained
    ]
    assert [piece.sequence for piece in duplicate_conversation] == [piece.sequence for piece in retained]
    assert [piece.original_prompt_id for piece in duplicate_conversation] == [
        piece.original_prompt_id for piece in retained
    ]
    assert {piece.id for piece in duplicate_conversation}.isdisjoint(piece.id for piece in source)
    assert [
        piece.model_dump() for piece in await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)
    ] == [piece.model_dump() for piece in source]


async def test_duplicate_conversation_excluding_last_turn_not_score(sqlite_instance: MemoryInterface):
    conversation_id = str(uuid4())
    prompt_id_1 = uuid4()
    prompt_id_2 = uuid4()
    pieces = [
        MessagePiece(
            id=prompt_id_1,
            role="user",
            original_value="original prompt text",
            converted_value="Hello, how are you?",
            conversation_id=conversation_id,
            sequence=0,
        ),
        MessagePiece(
            id=prompt_id_2,
            role="assistant",
            original_value="original prompt text",
            converted_value="I'm fine, thank you!",
            conversation_id=conversation_id,
            sequence=1,
        ),
        MessagePiece(
            role="user",
            original_value="original prompt text",
            converted_value="That's good.",
            conversation_id=conversation_id,
            sequence=2,
        ),
        MessagePiece(
            role="assistant",
            original_value="original prompt text",
            converted_value="Thanks.",
            conversation_id=conversation_id,
            sequence=3,
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))
    (await sqlite_instance.add_scores_to_memory_async(scores=scores))

    new_conversation_id = await sqlite_instance.duplicate_conversation_excluding_last_turn_async(
        conversation_id=conversation_id,
    )
    new_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=new_conversation_id)
    new_pieces_ids = [str(p.id) for p in new_pieces]
    assert len(new_pieces) == 2
    assert new_pieces[0].original_prompt_id == prompt_id_1
    assert new_pieces[1].original_prompt_id == prompt_id_2
    assert new_pieces[0].id != prompt_id_1
    assert new_pieces[1].id != prompt_id_2
    # The duplicate prompts ids should not have scores so only two scores are returned
    assert (
        len(
            await sqlite_instance.get_prompt_scores_async(
                prompt_ids=[str(prompt_id_1), str(prompt_id_2)] + new_pieces_ids
            )
        )
        == 2
    )


async def test_duplicate_conversation_excluding_last_turn_same_attack(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))
    assert len(await sqlite_instance.get_message_pieces_async()) == 4

    new_conversation_id1 = await sqlite_instance.duplicate_conversation_excluding_last_turn_async(
        conversation_id=conversation_id_1,
    )

    all_memory = await sqlite_instance.get_message_pieces_async()
    assert len(all_memory) == 6

    duplicate_conversation = await sqlite_instance.get_message_pieces_async(conversation_id=new_conversation_id1)
    assert len(duplicate_conversation) == 2

    for piece in duplicate_conversation:
        assert piece.sequence < 2


async def test_duplicate_conversation_creates_new_ids(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[original_piece]))

    new_conversation_id = await sqlite_instance.duplicate_conversation_async(
        conversation_id=conversation_id,
    )

    original_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)
    new_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=new_conversation_id)

    assert len(original_pieces) == 1
    assert len(new_pieces) == 1

    # IDs should be different
    assert original_pieces[0].id != new_pieces[0].id

    # Content should be preserved
    assert original_pieces[0].original_value == new_pieces[0].original_value
    assert original_pieces[0].converted_value == new_pieces[0].converted_value


async def test_duplicate_conversation_preserves_original_prompt_id(sqlite_instance: MemoryInterface):
    """Test that duplicated conversation preserves original_prompt_id for tracing."""
    attack1 = PromptSendingAttack(objective_target=get_mock_target())
    conversation_id = "test-conv-456"
    original_piece = MessagePiece(
        role="user",
        original_value="traceable prompt",
        conversation_id=conversation_id,
        sequence=1,
    )
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[original_piece]))
    original_prompt_id = original_piece.original_prompt_id

    new_conversation_id = await sqlite_instance.duplicate_conversation_async(
        conversation_id=conversation_id,
    )

    new_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=new_conversation_id)

    # original_prompt_id should be preserved for tracing
    assert new_pieces[0].original_prompt_id == original_prompt_id


async def test_duplicate_conversation_with_multiple_pieces(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

    new_conversation_id = await sqlite_instance.duplicate_conversation_async(
        conversation_id=conversation_id,
    )

    original_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)
    new_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=new_conversation_id)

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


async def test_add_message_pieces_to_memory_calls_validate(sqlite_instance: MemoryInterface):
    message = MagicMock(Message)
    message.message_pieces = [MagicMock(MessagePiece, not_in_memory=False, conversation_id="test-conversation")]
    with (
        patch.object(sqlite_instance, "_execute_add_message_pieces_to_memory"),
        patch("pyrit.memory.memory_interface.MemoryInterface._update_sequence"),
    ):
        (await sqlite_instance.add_message_to_memory_async(request=message))
    assert message.validate.called


@pytest.mark.parametrize("bad_id", [None, "", "   "])
async def test_add_message_pieces_to_memory_raises_when_conversation_id_missing(
    sqlite_instance: MemoryInterface, bad_id
):
    piece = MessagePiece(role="user", original_value="hello", conversation_id=bad_id)
    with pytest.raises(ValueError, match="conversation_id"):
        (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[piece]))


@pytest.mark.parametrize("bad_id", [None, "", "   "])
async def test_add_message_to_memory_raises_when_conversation_id_missing(sqlite_instance: MemoryInterface, bad_id):
    piece = MessagePiece(role="user", original_value="hello", conversation_id=bad_id)
    with pytest.raises(ValueError, match="conversation_id"):
        (await sqlite_instance.add_message_to_memory_async(request=Message(message_pieces=[piece])))


async def test_add_message_pieces_to_memory_skips_not_in_memory_without_conversation_id(
    sqlite_instance: MemoryInterface,
):
    # not_in_memory pieces are filtered out before persistence, so a missing
    # conversation_id on an ephemeral piece must not raise.
    ephemeral = MessagePiece(role="user", original_value="ephemeral", conversation_id=None)
    ephemeral.not_in_memory = True

    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[ephemeral]))

    assert (await sqlite_instance.get_message_pieces_async()) == []


async def test_add_conversation_to_memory_records_target_for_plain_message_writes(sqlite_instance: MemoryInterface):
    # Registering a conversation records its target once; subsequent message writes
    # do not take a target, yet target-filtered reads still find the messages.
    target_id = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://api.openai.com", "model_name": "gpt-4"},
    )
    conversation_id = "conv-registered"
    (
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_id)
        )
    )
    (
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[MessagePiece(role="user", original_value="hi", conversation_id=conversation_id)]
        )
    )

    metadata = sqlite_instance._get_conversation(conversation_id=conversation_id)
    assert metadata is not None
    assert metadata.target_identifier.hash == target_id.hash

    results = await sqlite_instance.get_message_pieces_async(
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


async def test_message_writes_without_registration_create_no_conversation_row(sqlite_instance: MemoryInterface):
    # Message writes no longer touch the Conversations table; conversation metadata
    # exists only when a conversation is explicitly registered.
    conversation_id = "conv-unregistered"
    (
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[MessagePiece(role="user", original_value="hi", conversation_id=conversation_id)]
        )
    )

    assert sqlite_instance._get_conversation(conversation_id=conversation_id) is None
    # The messages themselves still persist.
    assert len(await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)) == 1


async def test_add_conversation_to_memory_same_target_reregister_is_noop(sqlite_instance: MemoryInterface):
    # A conversation is held with exactly one target. Re-registering the same
    # conversation with the same target is idempotent (no error, no change) so that
    # per-turn registration during a multi-turn conversation is safe.
    conversation_id = "conv-reregister-same"
    target = ComponentIdentifier(
        class_name="OpenAIChatTarget", class_module="pyrit.prompt_target", params={"endpoint": "a"}
    )
    (
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target)
        )
    )
    (
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target)
        )
    )

    metadata = sqlite_instance._get_conversation(conversation_id=conversation_id)
    assert metadata is not None
    assert metadata.target_identifier.hash == target.hash


async def test_add_conversation_to_memory_different_target_reregister_raises(sqlite_instance: MemoryInterface):
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
    (
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_a)
        )
    )
    with pytest.raises(ValueError, match="already registered with a different target"):
        (
            await sqlite_instance.add_conversation_to_memory_async(
                conversation=Conversation(conversation_id=conversation_id, target_identifier=target_b)
            )
        )

    # The originally recorded target is left untouched.
    metadata = sqlite_instance._get_conversation(conversation_id=conversation_id)
    assert metadata is not None
    assert metadata.target_identifier.hash == target_a.hash


async def test_target_identifier_dual_write_reconstruction_is_equivalent(sqlite_instance: MemoryInterface):
    # Phase 1 dual-write invariant: reconstructing the target from the ConversationEntry
    # JSON column must be identical to reconstructing it from the deduped
    # TargetIdentifierEntry.identifier_json row, and the stored PK must match the
    # recomputed content hash. This is the safety net that lets phase 2 flip reads to
    # the FK path.
    from pyrit.memory.memory_models import ConversationEntry, TargetIdentifierEntry

    target = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://api.openai.com", "model_name": "gpt-4"},
    )
    conversation_id = "conv-dualwrite"
    (
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target)
        )
    )

    conv_entry = sqlite_instance._query_entries(
        ConversationEntry, conditions=ConversationEntry.conversation_id == conversation_id
    )[0]
    id_entry = sqlite_instance._query_entries(
        TargetIdentifierEntry, conditions=TargetIdentifierEntry.hash == target.hash
    )[0]

    from_conversation_json = ComponentIdentifier.model_validate(conv_entry.target_identifier)
    from_identifier_row = ComponentIdentifier.model_validate(id_entry.identifier_json)

    # Both reconstructions agree (equality is content-hash based) and match the FK / PK.
    assert from_conversation_json == from_identifier_row
    assert from_conversation_json.hash == target.hash
    assert id_entry.hash == target.hash
    assert conv_entry.target_identifier_hash == target.hash
    # Promoted columns are surfaced from params for querying.
    assert id_entry.endpoint == "https://api.openai.com"
    assert id_entry.model_name == "gpt-4"


async def test_target_identifier_row_is_deduped_across_conversations(sqlite_instance: MemoryInterface):
    # The same target reused across conversations is content-addressed, so it is stored
    # once: two conversations with an identical target share a single TargetIdentifiers row.
    from pyrit.memory.memory_models import TargetIdentifierEntry

    target = ComponentIdentifier(
        class_name="OpenAIChatTarget", class_module="pyrit.prompt_target", params={"endpoint": "shared"}
    )
    for cid in ("conv-dedup-a", "conv-dedup-b"):
        (
            await sqlite_instance.add_conversation_to_memory_async(
                conversation=Conversation(conversation_id=cid, target_identifier=target)
            )
        )

    rows = sqlite_instance._query_entries(TargetIdentifierEntry, conditions=TargetIdentifierEntry.hash == target.hash)
    assert len(rows) == 1


async def test_target_identifier_persists_inner_targets_and_edges(sqlite_instance: MemoryInterface):
    # A multi-target's inner targets are persisted as their own content-addressed rows
    # and linked to the parent via ordered TargetIdentifierChildren edges. Promoted
    # scalar columns are surfaced on each row for querying.
    from pyrit.memory.memory_models import TargetIdentifierChildEntry, TargetIdentifierEntry

    inner_a = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://a", "model_name": "gpt-a", "temperature": 0.5},
    )
    inner_b = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://b", "model_name": "gpt-b"},
    )
    multi = ComponentIdentifier(
        class_name="RoundRobinTarget",
        class_module="pyrit.prompt_target",
        children={"targets": [inner_a, inner_b]},
    )
    (
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id="conv-inner-a", target_identifier=inner_a)
        )
    )
    (
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id="conv-multi", target_identifier=multi)
        )
    )

    id_rows = sqlite_instance._query_entries(TargetIdentifierEntry)
    by_hash = {row.hash: row for row in id_rows}
    # Parent + both inner targets are each stored once (content-addressed).
    assert multi.hash in by_hash
    assert inner_a.hash in by_hash
    assert inner_b.hash in by_hash
    # Promoted scalar column surfaced from the inner target's params.
    assert by_hash[inner_a.hash].temperature == 0.5

    edges = sqlite_instance._query_entries(
        TargetIdentifierChildEntry, conditions=TargetIdentifierChildEntry.parent_hash == multi.hash
    )
    ordered = sorted(edges, key=lambda edge: edge.position)
    assert [(edge.position, edge.child_hash) for edge in ordered] == [(0, inner_a.hash), (1, inner_b.hash)]


def test_insert_conversation_rolls_back_and_reraises_on_db_error(sqlite_instance: MemoryInterface):
    # A DB failure during registration rolls back the session and propagates the error
    # rather than leaving a half-written Conversations row.
    from sqlalchemy.exc import SQLAlchemyError

    session = MagicMock()
    session.get.side_effect = SQLAlchemyError("boom")

    with patch.object(sqlite_instance, "_get_session", return_value=session):
        with pytest.raises(SQLAlchemyError, match="boom"):
            sqlite_instance._insert_conversation(conversation=Conversation(conversation_id="conv-fail"))

    session.rollback.assert_called_once()
    session.commit.assert_not_called()


async def test_add_message_pieces_to_memory_updates_sequence(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    for conversation in sample_conversations:
        conversation.conversation_id = sample_conversations[0].conversation_id
        conversation.role = sample_conversations[0].role
        conversation.sequence = 17

    with patch.object(sqlite_instance, "_execute_add_message_pieces_to_memory") as mock_add:
        (await sqlite_instance.add_message_to_memory_async(request=Message(message_pieces=sample_conversations)))
        assert mock_add.called

        args, kwargs = mock_add.call_args
        assert kwargs["message_pieces"][0].sequence == 0, "Sequence should be reset to 0"
        assert kwargs["message_pieces"][1].sequence == 0, "Sequence should be reset to 0"
        assert kwargs["message_pieces"][2].sequence == 0, "Sequence should be reset to 0"


async def test_add_message_pieces_to_memory_updates_sequence_with_prev_conversation(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    for conversation in sample_conversations:
        conversation.conversation_id = sample_conversations[0].conversation_id
        conversation.role = sample_conversations[0].role
        conversation.sequence = 17

    # insert one of these into memory
    (await sqlite_instance.add_message_to_memory_async(request=Message(message_pieces=sample_conversations)))

    with patch.object(sqlite_instance, "_execute_add_message_pieces_to_memory") as mock_add:
        (await sqlite_instance.add_message_to_memory_async(request=Message(message_pieces=sample_conversations)))
        assert mock_add.called

        args, kwargs = mock_add.call_args
        assert kwargs["message_pieces"][0].sequence == 1, "Sequence should increment previous conversation by 1"
        assert kwargs["message_pieces"][1].sequence == 1
        assert kwargs["message_pieces"][2].sequence == 1


async def test_insert_prompt_memories_inserts_embedding(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    request = Message(message_pieces=[sample_conversations[0]])

    embedding_mock = MagicMock()
    embedding_mock.generate_text_embedding.returns = [0, 1, 2]
    sqlite_instance.enable_embedding(embedding_model=embedding_mock)

    with (
        patch.object(sqlite_instance, "_execute_add_message_pieces_to_memory"),
        patch("pyrit.memory.sqlite_memory.SQLiteMemory._add_embeddings_to_memory") as mock_embedding,
    ):
        (await sqlite_instance.add_message_to_memory_async(request=request))

        assert mock_embedding.called
        assert embedding_mock.generate_text_embedding.called


async def test_insert_prompt_memories_not_inserts_embedding(
    sqlite_instance: MemoryInterface, sample_conversations: Sequence[MessagePiece]
):
    request = Message(message_pieces=[sample_conversations[0]])

    embedding_mock = MagicMock()
    embedding_mock.generate_text_embedding.returns = [0, 1, 2]
    sqlite_instance.enable_embedding(embedding_model=embedding_mock)
    sqlite_instance.disable_embedding()

    with (
        patch.object(sqlite_instance, "_execute_add_message_pieces_to_memory"),
        patch("pyrit.memory.sqlite_memory.SQLiteMemory._add_embeddings_to_memory") as mock_embedding,
    ):
        (await sqlite_instance.add_message_to_memory_async(request=request))

        mock_embedding.assert_not_called()


async def test_get_message_pieces_metadata(sqlite_instance: MemoryInterface):
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

    retrieved_entries = await sqlite_instance.get_message_pieces_async(prompt_metadata={"key2": "value2"})

    assert len(retrieved_entries) == 2  # Two entries should have the specific memory labels
    for retrieved_entry in retrieved_entries:
        assert "key2" in retrieved_entry.prompt_metadata


@pytest.mark.parametrize(
    "key, value",
    [("finish_reason", "content_filter"), ("status", "incomplete"), ("incomplete_reason", "max_output_tokens")],
)
async def test_get_message_pieces_captured_response_metadata(sqlite_instance: MemoryInterface, key: str, value: str):
    """The response metadata captured by the targets must be queryable after a round trip."""
    matching = MessagePiece(
        conversation_id=str(uuid4()),
        role="assistant",
        original_value="blocked",
    )
    other = MessagePiece(
        conversation_id=str(uuid4()),
        role="assistant",
        original_value="fine",
    )
    set_response_metadata(pieces=[matching], **{key: value})
    set_response_metadata(pieces=[other], **{key: "something_else"})
    sqlite_instance._insert_entries(entries=[PromptMemoryEntry(entry=matching), PromptMemoryEntry(entry=other)])

    retrieved = await sqlite_instance.get_message_pieces_async(prompt_metadata={key: value})

    assert len(retrieved) == 1
    assert retrieved[0].prompt_metadata[key] == value


@pytest.mark.parametrize("key", ["attack.id", "a-b", "sp ace", "with:colon"])
async def test_get_message_pieces_prompt_metadata_key_with_non_identifier_character(
    sqlite_instance: MemoryInterface, key: str
):
    """A free-form metadata key must be queryable whatever characters it holds.

    The key used to be spelled into the JSON path and reused as the bind-parameter
    name, so a dot read as a path step and a hyphen was not a legal parameter name:
    the query raised ``ArgumentError: This text() construct doesn't define a bound
    parameter named ...`` instead of filtering.
    """
    matching = MessagePiece(
        conversation_id=str(uuid4()),
        role="user",
        original_value="targeted",
        prompt_metadata={key: "value"},
    )
    other = MessagePiece(
        conversation_id=str(uuid4()),
        role="user",
        original_value="unrelated",
        prompt_metadata={key: "something_else"},
    )
    sqlite_instance._insert_entries(entries=[PromptMemoryEntry(entry=matching), PromptMemoryEntry(entry=other)])

    retrieved = await sqlite_instance.get_message_pieces_async(prompt_metadata={key: "value"})

    assert len(retrieved) == 1
    assert retrieved[0].original_value == "targeted"


async def test_get_message_pieces_prompt_metadata_multiple_non_identifier_keys(sqlite_instance: MemoryInterface):
    """Several free-form keys in one query each get their own bound path and value."""
    metadata = {"attack.id": "a", "a-b": "b", "plain": "c"}
    piece = MessagePiece(
        conversation_id=str(uuid4()),
        role="user",
        original_value="hit",
        prompt_metadata=metadata,
    )
    sqlite_instance._insert_entries(entries=[PromptMemoryEntry(entry=piece)])

    assert len(await sqlite_instance.get_message_pieces_async(prompt_metadata=metadata)) == 1
    assert len(await sqlite_instance.get_message_pieces_async(prompt_metadata={"attack.id": "a", "a-b": "b"})) == 1
    assert len(await sqlite_instance.get_message_pieces_async(prompt_metadata={"attack.id": "a", "a-b": "nope"})) == 0


@pytest.mark.parametrize(
    ("literal_key", "other_metadata"),
    [
        ('branch"."leaf', {"branch": {"leaf": "value"}}),
        (r"\u0061", {"a": "value"}),
        ("plain", {"plain ": "value"}),
        ("plain ", {"plain": "value"}),
        ("", {" ": "value"}),
        (" ", {"": "value"}),
        ("café", {"café ": "value"}),
        ("café ", {"café": "value"}),
    ],
)
async def test_get_message_pieces_prompt_metadata_matches_literal_json_keys(
    sqlite_instance: MemoryInterface, literal_key: str, other_metadata: dict[str, object]
):
    matching = MessagePiece(
        conversation_id=str(uuid4()),
        role="user",
        original_value="literal",
        prompt_metadata={literal_key: "value"},
    )
    other = MessagePiece(
        conversation_id=str(uuid4()),
        role="user",
        original_value="other",
        prompt_metadata=other_metadata,
    )
    sqlite_instance._insert_entries(entries=[PromptMemoryEntry(entry=matching), PromptMemoryEntry(entry=other)])

    retrieved = await sqlite_instance.get_message_pieces_async(prompt_metadata={literal_key: "value"})

    assert [piece.original_value for piece in retrieved] == ["literal"]


@pytest.mark.parametrize(
    ("literal_key", "other_metadata"),
    [
        ('branch"."leaf', {"branch": {"leaf": "value"}}),
        (r"\u0061", {"a": "value"}),
        ("plain", {"plain ": "value"}),
        ("plain ", {"plain": "value"}),
        ("", {" ": "value"}),
        (" ", {"": "value"}),
        ("café", {"café ": "value"}),
        ("café ", {"café": "value"}),
    ],
)
async def test_seed_metadata_retrieval_matches_literal_json_keys(
    sqlite_instance: MemoryInterface, literal_key: str, other_metadata: dict[str, object]
):
    literal_group_id, nested_group_id = uuid4(), uuid4()
    seeds = [
        SeedPrompt(
            value="literal first",
            data_type="text",
            metadata={literal_key: "value"},
            prompt_group_id=literal_group_id,
        ),
        SeedPrompt(
            value="literal second",
            data_type="text",
            metadata={literal_key: "value"},
            prompt_group_id=literal_group_id,
        ),
        SeedPrompt(
            value="nested",
            data_type="text",
            metadata=other_metadata,
            prompt_group_id=nested_group_id,
        ),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="tester")

    retrieved = await sqlite_instance.get_seeds_async(metadata={literal_key: "value"})
    assert {seed.value for seed in retrieved} == {"literal first", "literal second"}


@pytest.mark.parametrize(
    ("literal_key", "other_metadata"),
    [
        ('branch"."leaf', {"branch": {"leaf": "value"}}),
        (r"\u0061", {"a": "value"}),
        ("plain", {"plain ": "value"}),
        ("plain ", {"plain": "value"}),
        ("", {" ": "value"}),
        (" ", {"": "value"}),
        ("café", {"café ": "value"}),
        ("café ", {"café": "value"}),
    ],
)
async def test_seed_removal_matches_literal_json_keys(
    sqlite_instance: MemoryInterface, literal_key: str, other_metadata: dict[str, object]
):
    seeds = [
        SeedPrompt(value="literal", data_type="text", metadata={literal_key: "value"}),
        SeedPrompt(value="nested", data_type="text", metadata=other_metadata),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="tester")

    removed = await sqlite_instance.remove_seeds_from_memory_async(metadata={literal_key: "value"})

    remaining = await sqlite_instance.get_seeds_async()
    assert [seed.value for seed in remaining] == ["nested"]
    assert removed == 1


@pytest.mark.parametrize(
    ("literal_key", "other_metadata"),
    [
        ('branch"."leaf', {"branch": {"leaf": "value"}}),
        (r"\u0061", {"a": "value"}),
        ("plain", {"plain ": "value"}),
        ("plain ", {"plain": "value"}),
        ("", {" ": "value"}),
        (" ", {"": "value"}),
        ("café", {"café ": "value"}),
        ("café ", {"café": "value"}),
    ],
)
async def test_seed_group_removal_matches_literal_json_keys(
    sqlite_instance: MemoryInterface, literal_key: str, other_metadata: dict[str, object]
):
    literal_group_id, nested_group_id = uuid4(), uuid4()
    seeds = [
        SeedPrompt(
            value="literal first",
            data_type="text",
            metadata={literal_key: "value"},
            prompt_group_id=literal_group_id,
        ),
        SeedPrompt(
            value="literal second",
            data_type="text",
            metadata={literal_key: "value"},
            prompt_group_id=literal_group_id,
        ),
        SeedPrompt(
            value="nested",
            data_type="text",
            metadata=other_metadata,
            prompt_group_id=nested_group_id,
        ),
    ]
    await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="tester")

    removed = await sqlite_instance.remove_seed_groups_from_memory_async(metadata={literal_key: "value"})

    remaining = await sqlite_instance.get_seeds_async()
    assert [seed.value for seed in remaining] == ["nested"]
    assert removed == 2


async def test_get_message_pieces_id(sqlite_instance: MemoryInterface):
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

    retrieved_entries = await sqlite_instance.get_message_pieces_async(prompt_ids=[id_1, id_2])

    assert len(retrieved_entries) == 2
    assert_original_value_in_list("Hello 1", retrieved_entries)
    assert_original_value_in_list("Hello 2", retrieved_entries)


async def test_get_message_pieces_sent_after(sqlite_instance: MemoryInterface):
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

    entries[0].timestamp = datetime(2022, 12, 25, 15, 30, 0, tzinfo=UTC)
    entries[1].timestamp = datetime(2022, 12, 25, 15, 30, 0, tzinfo=UTC)

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = await sqlite_instance.get_message_pieces_async(sent_after=datetime(2024, 1, 1, tzinfo=UTC))

    assert len(retrieved_entries) == 1
    assert "Hello 3" in retrieved_entries[0].original_value


async def test_get_message_pieces_sent_before(sqlite_instance: MemoryInterface):
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

    entries[0].timestamp = datetime(2022, 12, 25, 15, 30, 0, tzinfo=UTC)
    entries[1].timestamp = datetime(2021, 12, 25, 15, 30, 0, tzinfo=UTC)

    sqlite_instance._insert_entries(entries=entries)

    retrieved_entries = await sqlite_instance.get_message_pieces_async(sent_before=datetime(2024, 1, 1, tzinfo=UTC))

    assert len(retrieved_entries) == 2
    assert_original_value_in_list("Hello 1", retrieved_entries)
    assert_original_value_in_list("Hello 2", retrieved_entries)


async def test_get_message_pieces_by_value(sqlite_instance: MemoryInterface):
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
    retrieved_entries = await sqlite_instance.get_message_pieces_async(converted_values=["Hello 2", "Hello 3"])

    assert len(retrieved_entries) == 2
    assert_original_value_in_list("Hello 2", retrieved_entries)
    assert_original_value_in_list("Hello 3", retrieved_entries)


async def test_get_message_pieces_by_hash(sqlite_instance: MemoryInterface):
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

    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=entries))
    retrieved_entries = await sqlite_instance.get_message_pieces_async(converted_value_sha256=["hash1"])

    assert len(retrieved_entries) == 2
    assert_original_value_in_list("Hello 1", retrieved_entries)
    assert_original_value_in_list("Hello 2", retrieved_entries)


async def test_get_message_pieces_sorts(
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

    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=sample_conversations))

    response = await sqlite_instance.get_message_pieces_async()

    current_value = response[0].conversation_id
    for obj in response[1:]:
        new_value = obj.conversation_id
        if new_value != current_value and any(
            o.conversation_id == current_value for o in response[response.index(obj) :]
        ):
            raise AssertionError("Conversation IDs are not grouped together")


async def test_message_piece_scores_duplicate_piece(sqlite_instance: MemoryInterface):
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

    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

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
    (await sqlite_instance.add_scores_to_memory_async(scores=[score]))

    # Both the original and the duplicate piece resolve back to the same score
    # via get_prompt_scores, which queries ScoreEntry by original_prompt_id.
    scores_for_original = await sqlite_instance.get_prompt_scores_async(prompt_ids=[str(original_id)])
    scores_for_duplicate = await sqlite_instance.get_prompt_scores_async(prompt_ids=[str(duplicate_id)])

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
        await set_message_piece_sha256_async(entry)

    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=entries))
    retrieved_entries = await sqlite_instance.get_message_pieces_async()

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
    retrieved_prompts = await sqlite_instance.get_seeds_async(value_sha256=[seed_prompt.value_sha256])
    assert len(retrieved_prompts) == 1
    assert retrieved_prompts[0].value_sha256 == seed_prompt.value_sha256


async def test_get_request_from_response_success(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

    # Get the conversation and extract the response
    conversation = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    response = conversation[1]

    # Retrieve the request that produced this response
    request = await sqlite_instance.get_request_from_response_async(response=response)

    assert request.api_role == "user"
    assert request.sequence == 0
    assert request.get_value() == "What is the weather?"
    assert request.conversation_id == conversation_id


@pytest.mark.parametrize("bad_conversation_id", ["", None])
async def test_get_conversation_messages_rejects_falsy_conversation_id(
    sqlite_instance: MemoryInterface, bad_conversation_id
):
    """A falsy conversation_id must raise instead of skipping the filter and returning every conversation."""
    pieces = [
        MessagePiece(
            role="user",
            original_value="conversation one",
            converted_value="conversation one",
            conversation_id=str(uuid4()),
            sequence=0,
        ),
        MessagePiece(
            role="user",
            original_value="conversation two",
            converted_value="conversation two",
            conversation_id=str(uuid4()),
            sequence=0,
        ),
    ]
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

    with pytest.raises(ValueError, match="requires a non-empty conversation_id"):
        (await sqlite_instance.get_conversation_messages_async(conversation_id=bad_conversation_id))


async def test_get_request_from_response_multi_turn_conversation(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

    conversation = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)

    # Test getting request for the second response
    second_response = conversation[3]
    second_request = await sqlite_instance.get_request_from_response_async(response=second_response)

    assert second_request.api_role == "user"
    assert second_request.sequence == 2
    assert second_request.get_value() == "Second question"


async def test_get_request_from_response_raises_error_for_non_assistant_role(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

    conversation = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    user_message = conversation[0]

    with pytest.raises(ValueError, match="The provided request is not a response \\(role must be 'assistant'\\)."):
        (await sqlite_instance.get_request_from_response_async(response=user_message))


async def test_get_request_from_response_raises_error_for_sequence_less_than_one(sqlite_instance: MemoryInterface):
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
    (await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces))

    conversation = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    response_without_request = conversation[0]

    with pytest.raises(ValueError, match="The provided request does not have a preceding request \\(sequence < 1\\)."):
        (await sqlite_instance.get_request_from_response_async(response=response_without_request))


async def test_get_request_from_response_with_non_zero_starting_sequence(sqlite_instance: MemoryInterface):
    """Test get_request_from_response when conversation starts at a non-zero sequence number."""
    conversation_id = str(uuid4())

    pieces = [
        MessagePiece(
            role="user",
            original_value="Starting question at sequence 10",
            converted_value="Starting question at sequence 10",
            conversation_id=conversation_id,
            sequence=10,
        ),
        MessagePiece(
            role="assistant",
            original_value="Starting answer at sequence 11",
            converted_value="Starting answer at sequence 11",
            conversation_id=conversation_id,
            sequence=11,
        ),
    ]
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces)

    conversation = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    response = conversation[1]

    request = await sqlite_instance.get_request_from_response_async(response=response)

    assert request.api_role == "user"
    assert request.sequence == 10
    assert request.get_value() == "Starting question at sequence 10"


async def test_get_request_from_response_with_sequence_gaps(sqlite_instance: MemoryInterface):
    """Test get_request_from_response when conversation contains gaps in sequence numbers."""
    conversation_id = str(uuid4())

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
            original_value="Question after gap",
            converted_value="Question after gap",
            conversation_id=conversation_id,
            sequence=5,
        ),
        MessagePiece(
            role="assistant",
            original_value="Answer after gap",
            converted_value="Answer after gap",
            conversation_id=conversation_id,
            sequence=8,
        ),
    ]
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces)

    conversation = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    response = conversation[3]

    request = await sqlite_instance.get_request_from_response_async(response=response)

    assert request.api_role == "user"
    assert request.sequence == 5
    assert request.get_value() == "Question after gap"


async def test_get_request_from_response_raises_error_when_no_preceding_messages(sqlite_instance: MemoryInterface):
    """Test get_request_from_response raises ValueError when no preceding messages exist even if sequence >= 1."""
    conversation_id = str(uuid4())

    pieces = [
        MessagePiece(
            role="assistant",
            original_value="Orphaned response",
            converted_value="Orphaned response",
            conversation_id=conversation_id,
            sequence=5,
        ),
    ]
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces)

    conversation = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    response = conversation[0]

    with pytest.raises(ValueError, match="does not have a preceding request"):
        await sqlite_instance.get_request_from_response_async(response=response)


async def test_get_request_from_response_after_tool_call(sqlite_instance: MemoryInterface):
    """Test get_request_from_response returns the user request, not the tool output, for an answer after a tool call."""
    conversation_id = str(uuid4())

    # A target's tool loop stores the function call and its output as messages between the request and the answer.
    pieces = [
        MessagePiece(role="user", original_value="First question", conversation_id=conversation_id, sequence=0),
        MessagePiece(role="assistant", original_value="First answer", conversation_id=conversation_id, sequence=1),
        MessagePiece(
            role="user", original_value="What is the weather in Seattle?", conversation_id=conversation_id, sequence=2
        ),
        MessagePiece(
            role="assistant",
            original_value=json.dumps({"type": "function_call", "call_id": "call_1", "name": "get_weather"}),
            original_value_data_type="function_call",
            conversation_id=conversation_id,
            sequence=3,
        ),
        MessagePiece(
            role="tool",
            original_value=json.dumps({"type": "function_call_output", "call_id": "call_1", "output": "sunny"}),
            original_value_data_type="function_call_output",
            conversation_id=conversation_id,
            sequence=4,
        ),
        MessagePiece(
            role="assistant", original_value="It's sunny in Seattle.", conversation_id=conversation_id, sequence=5
        ),
    ]
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces)

    conversation = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    response = conversation[5]

    request = await sqlite_instance.get_request_from_response_async(response=response)

    assert request.api_role == "user"
    assert request.sequence == 2
    assert request.get_value() == "What is the weather in Seattle?"


@pytest.mark.parametrize("request_role", ["user", "system", "developer", "assistant", "tool"])
@pytest.mark.parametrize("duplicate", [False, True], ids=["original", "cloned"])
@pytest.mark.parametrize("with_history", [False, True], ids=["first-turn", "later-turn"])
async def test_get_request_from_response_preserves_marked_request_async(
    *, sqlite_instance: MemoryInterface, request_role: ChatMessageRole, duplicate: bool, with_history: bool
) -> None:
    conversation_id = str(uuid4())
    pieces = [
        MessagePiece(
            role="user",
            original_value="Earlier request",
            conversation_id=conversation_id,
            sequence=0,
            prompt_metadata={RequestTraceContext.REQUEST_METADATA_KEY: 1},
        ),
        MessagePiece(role="assistant", original_value="Earlier answer", conversation_id=conversation_id, sequence=1),
        MessagePiece(role=request_role, original_value="First piece", conversation_id=conversation_id, sequence=5),
        MessagePiece(
            role=request_role,
            original_value="Second piece",
            conversation_id=conversation_id,
            sequence=5,
            prompt_metadata={RequestTraceContext.REQUEST_METADATA_KEY: 1},
        ),
        MessagePiece(
            role="assistant",
            original_value="call",
            original_value_data_type="function_call",
            conversation_id=conversation_id,
            sequence=8,
        ),
        MessagePiece(
            role="tool",
            original_value="output",
            original_value_data_type="function_call_output",
            conversation_id=conversation_id,
            sequence=10,
        ),
        MessagePiece(role="assistant", original_value="Final answer", conversation_id=conversation_id, sequence=12),
        MessagePiece(
            role="user",
            original_value="Later request",
            conversation_id=conversation_id,
            sequence=20,
            prompt_metadata={RequestTraceContext.REQUEST_METADATA_KEY: 1},
        ),
    ]
    if not with_history:
        pieces = pieces[2:]
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=list(reversed(pieces)))
    if duplicate:
        conversation_id = await sqlite_instance.duplicate_conversation_async(conversation_id=conversation_id)
    messages = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    response = next(message for message in messages if message.sequence == 12)

    request = await sqlite_instance.get_request_from_response_async(response=response)

    assert request.api_role == request_role
    assert request.sequence == 5
    assert request.conversation_id == conversation_id
    assert {piece.original_value for piece in request.message_pieces} == {"First piece", "Second piece"}


async def test_get_request_from_response_preserves_newer_unmarked_user_request_async(
    *, sqlite_instance: MemoryInterface
) -> None:
    conversation_id = str(uuid4())
    pieces = [
        MessagePiece(
            role="user",
            original_value="Earlier request",
            conversation_id=conversation_id,
            sequence=0,
            prompt_metadata={RequestTraceContext.REQUEST_METADATA_KEY: 1},
        ),
        MessagePiece(role="assistant", original_value="Earlier answer", conversation_id=conversation_id, sequence=1),
        MessagePiece(role="user", original_value="Current request", conversation_id=conversation_id, sequence=2),
        MessagePiece(role="assistant", original_value="Current answer", conversation_id=conversation_id, sequence=3),
    ]
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces)

    request = await sqlite_instance.get_request_from_response_async(response=pieces[-1].to_message())

    assert request.sequence == 2
    assert request.get_value() == "Current request"


async def test_get_request_from_response_without_user_message_returns_previous_message(
    sqlite_instance: MemoryInterface,
):
    """Test get_request_from_response falls back to the previous message when no user message precedes the response."""
    conversation_id = str(uuid4())

    pieces = [
        MessagePiece(role="system", original_value="You are helpful.", conversation_id=conversation_id, sequence=0),
        MessagePiece(role="assistant", original_value="Hello!", conversation_id=conversation_id, sequence=1),
        MessagePiece(role="assistant", original_value="How can I help?", conversation_id=conversation_id, sequence=2),
    ]
    await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=pieces)

    conversation = await sqlite_instance.get_conversation_messages_async(conversation_id=conversation_id)
    response = conversation[2]

    request = await sqlite_instance.get_request_from_response_async(response=response)

    assert request.sequence == 1
    assert request.get_value() == "Hello!"


async def test_get_message_pieces_by_attack_identifier_filter(sqlite_instance: MemoryInterface):
    attack1 = PromptSendingAttack(objective_target=get_mock_target())

    # IdentifierType.ATTACK is no longer stamped on message pieces, so the piece-level
    # identifier filter rejects it. Attack filtering now goes through get_attack_results.
    with pytest.raises(ValueError, match="does not support identifier type"):
        (
            await sqlite_instance.get_message_pieces_async(
                identifier_filters=[
                    IdentifierFilter(
                        identifier_type=IdentifierType.ATTACK,
                        property_path="$.hash",
                        value=attack1.get_identifier().hash,
                        partial_match=False,
                    )
                ],
            )
        )


async def test_get_message_pieces_by_target_identifier_filter(sqlite_instance: MemoryInterface):
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

    (
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id="conv-openai", target_identifier=target_id_1)
        )
    )
    (
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="Hello OpenAI",
                    conversation_id="conv-openai",
                ),
            ],
        )
    )
    (
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(conversation_id="conv-azure", target_identifier=target_id_2)
        )
    )
    (
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="Hello Azure",
                    conversation_id="conv-azure",
                ),
            ],
        )
    )

    # Filter by target hash
    results = await sqlite_instance.get_message_pieces_async(
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
    results = await sqlite_instance.get_message_pieces_async(
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
    results = await sqlite_instance.get_message_pieces_async(
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


async def test_get_message_pieces_by_converter_identifier_filter_with_array_element_path(
    sqlite_instance: MemoryInterface,
):
    converter_a = ComponentIdentifier(
        class_name="Base64Converter",
        class_module="pyrit.converter",
    )
    converter_b = ComponentIdentifier(
        class_name="ROT13Converter",
        class_module="pyrit.converter",
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
    results = await sqlite_instance.get_message_pieces_async(
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
    results = await sqlite_instance.get_message_pieces_async(
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
    results = await sqlite_instance.get_message_pieces_async(
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
