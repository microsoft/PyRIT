# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
import time
import uuid
import warnings
from datetime import datetime, timedelta
from typing import MutableSequence
from unittest.mock import MagicMock

import pytest
from unit.mocks import MockPromptTarget, get_sample_conversations

from pyrit.executor.attack import PromptSendingAttack
from pyrit.identifiers import ScorerIdentifier
from pyrit.models import (
    Message,
    MessagePiece,
    Score,
    construct_response_from_request,
    group_conversation_message_pieces_by_sequence,
    group_message_pieces_into_conversations,
    sort_message_pieces,
)
from pyrit.prompt_converter import Base64Converter


@pytest.fixture
def sample_conversations() -> MutableSequence[Message]:
    return get_sample_conversations()


def test_id_set():
    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
    )
    assert entry.id is not None


def test_datetime_set():
    now = datetime.now()
    time.sleep(0.1)
    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
    )
    assert entry.timestamp > now


def test_converters_serialize():
    converter_identifiers = [Base64Converter().get_identifier()]
    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
        converter_identifiers=converter_identifiers,
    )

    assert len(entry.converter_identifiers) == 1

    converter = entry.converter_identifiers[0]

    assert converter["__type__"] == "Base64Converter"
    assert converter["__module__"] == "pyrit.prompt_converter.base64_converter"


def test_prompt_targets_serialize(patch_central_database):
    target = MockPromptTarget()
    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
        prompt_target_identifier=target.get_identifier(),
    )
    assert patch_central_database.called
    assert entry.prompt_target_identifier["__type__"] == "MockPromptTarget"
    assert entry.prompt_target_identifier["__module__"] == "unit.mocks"


def test_executors_serialize():
    attack = PromptSendingAttack(objective_target=MagicMock())

    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
        attack_identifier=attack.get_identifier(),
    )

    assert entry.attack_identifier["id"] is not None
    assert entry.attack_identifier["__type__"] == "PromptSendingAttack"
    assert entry.attack_identifier["__module__"] == "pyrit.executor.attack.single_turn.prompt_sending"


@pytest.mark.asyncio
async def test_hashes_generated():
    entry = MessagePiece(
        role="user",
        original_value="Hello1",
        converted_value="Hello2",
    )
    await entry.set_sha256_values_async()
    assert entry.original_value_sha256 == "948edbe7ede5aa7423476ae29dcd7d61e7711a071aea0d83698377effa896525"
    assert entry.converted_value_sha256 == "be98c2510e417405647facb89399582fc499c3de4452b3014857f92e6baad9a9"


@pytest.mark.asyncio
async def test_hashes_generated_files():
    filename = ""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        filename = f.name
        f.write(b"Hello1")
        f.flush()
        f.close()
        entry = MessagePiece(
            role="user",
            original_value=filename,
            converted_value=filename,
            original_value_data_type="image_path",
            converted_value_data_type="audio_path",
        )
        await entry.set_sha256_values_async()
        assert entry.original_value_sha256 == "948edbe7ede5aa7423476ae29dcd7d61e7711a071aea0d83698377effa896525"
        assert entry.converted_value_sha256 == "948edbe7ede5aa7423476ae29dcd7d61e7711a071aea0d83698377effa896525"

    os.remove(filename)


@pytest.mark.asyncio
async def test_converted_datatype_default():
    filename = ""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        filename = f.name
        f.write(b"Hello1")
        f.flush()
        f.close()
        entry = MessagePiece(
            role="user",
            original_value=filename,
            original_value_data_type="image_path",
        )
        assert entry.converted_value_data_type == "image_path"
        assert entry.converted_value == filename

    os.remove(filename)


def test_hashes_generated_files_unknown_type():
    with pytest.raises(ValueError, match="is not a valid data type."):
        MessagePiece(
            role="user",
            original_value="Hello1",
            original_value_data_type="new_unknown_type",  # type: ignore
        )


def test_message_get_value(sample_conversations: MutableSequence[Message]):
    # Create a simple valid response for testing
    piece = MessagePiece(
        role="user", conversation_id="test", original_value="Hello, how are you?", converted_value="Hello, how are you?"
    )
    message = Message(message_pieces=[piece])
    assert message.get_value() == "Hello, how are you?"

    with pytest.raises(IndexError):
        message.get_value(3)


def test_message_get_values(sample_conversations: MutableSequence[Message]):
    # Create a valid response with multiple user pieces with same conversation ID and sequence
    piece1 = MessagePiece(
        role="user",
        conversation_id="test",
        sequence=1,
        original_value="Hello, how are you?",
        converted_value="Hello, how are you?",
    )
    piece2 = MessagePiece(
        role="user",
        conversation_id="test",
        sequence=1,  # Same sequence for consistent validation
        original_value="Another message",
        converted_value="Another message",
    )
    message = Message(message_pieces=[piece1, piece2])
    assert message.get_values() == ["Hello, how are you?", "Another message"]


def test_message_validate(sample_conversations: MutableSequence[Message]):
    for c in sample_conversations:
        c.validate()


def test_message_empty_throws():
    with pytest.raises(ValueError, match="Message must have at least one message piece."):
        Message(message_pieces=[])


def test_message_validate_conversation_id_throws():
    # Create pieces with different conversation IDs (this should fail validation)
    piece1 = MessagePiece(role="user", conversation_id="conv1", original_value="test1")
    piece2 = MessagePiece(role="user", conversation_id="conv2", original_value="test2")

    with pytest.raises(ValueError, match="Conversation ID mismatch."):
        Message(message_pieces=[piece1, piece2])


def test_message_inconsistent_roles_throws():
    # Create pieces with mixed roles (this should fail validation)
    piece1 = MessagePiece(role="user", conversation_id="conv1", original_value="test1")
    piece2 = MessagePiece(role="assistant", conversation_id="conv1", original_value="test2")

    with pytest.raises(ValueError, match="Inconsistent roles within the same message entry."):
        Message(message_pieces=[piece1, piece2])


def test_message_inconsistent_sequence_throws():
    # Create pieces with different sequences (this should fail validation during construction)
    piece1 = MessagePiece(role="user", conversation_id="conv1", sequence=1, original_value="test1")
    piece2 = MessagePiece(role="user", conversation_id="conv1", sequence=2, original_value="test2")

    with pytest.raises(ValueError, match="Inconsistent sequences within the same message entry."):
        Message(message_pieces=[piece1, piece2])


def test_group_conversation_message_pieces_throws():
    # Create pieces with different conversation IDs to trigger error
    pieces = [
        MessagePiece(role="user", conversation_id="conv1", original_value="test1"),
        MessagePiece(role="user", conversation_id="conv2", original_value="test2"),
    ]
    with pytest.raises(
        ValueError,
        match="All message pieces must be from the same conversation",
    ):
        group_conversation_message_pieces_by_sequence(pieces)


def test_group_message_pieces_into_conversations_multiple_conversations():
    """Test grouping pieces from multiple conversations."""
    pieces = [
        # Conversation 1 - each sequence/role combination is separate
        MessagePiece(role="user", conversation_id="conv1", sequence=0, original_value="Conv1 User Seq0"),
        MessagePiece(role="assistant", conversation_id="conv1", sequence=1, original_value="Conv1 Asst Seq1"),
        MessagePiece(role="user", conversation_id="conv1", sequence=2, original_value="Conv1 User Seq2"),
        # Conversation 2
        MessagePiece(role="user", conversation_id="conv2", sequence=0, original_value="Conv2 User Seq0"),
        MessagePiece(role="assistant", conversation_id="conv2", sequence=1, original_value="Conv2 Asst Seq1"),
        # Conversation 3
        MessagePiece(role="user", conversation_id="conv3", sequence=0, original_value="Conv3 User Seq0"),
    ]

    conversations = group_message_pieces_into_conversations(pieces)

    # Should get 3 conversations
    assert len(conversations) == 3

    # Find each conversation
    conv1 = next((c for c in conversations if c[0].message_pieces[0].conversation_id == "conv1"), None)
    conv2 = next((c for c in conversations if c[0].message_pieces[0].conversation_id == "conv2"), None)
    conv3 = next((c for c in conversations if c[0].message_pieces[0].conversation_id == "conv3"), None)

    assert conv1 is not None
    assert conv2 is not None
    assert conv3 is not None

    # Conv1 should have 3 sequences (0, 1, 2)
    assert len(conv1) == 3
    # Conv2 should have 2 sequences (0, 1)
    assert len(conv2) == 2
    # Conv3 should have 1 sequence (0)
    assert len(conv3) == 1


def test_group_message_pieces_into_conversations_empty_list():
    """Test grouping with empty list returns empty list."""
    result = group_message_pieces_into_conversations([])
    assert result == []


def test_group_message_pieces_into_conversations_single_conversation():
    """Test that function works correctly when all pieces are from same conversation."""
    pieces = [
        MessagePiece(role="user", conversation_id="conv1", sequence=0, original_value="User Seq0"),
        MessagePiece(role="assistant", conversation_id="conv1", sequence=1, original_value="Asst Seq1"),
        MessagePiece(role="user", conversation_id="conv1", sequence=2, original_value="User Seq2"),
    ]

    conversations = group_message_pieces_into_conversations(pieces)

    assert len(conversations) == 1  # 1 conversation
    assert len(conversations[0]) == 3  # 3 sequences in that conversation
    # Each sequence should have 1 piece (since each has a different role)
    assert len(conversations[0][0].message_pieces) == 1
    assert len(conversations[0][1].message_pieces) == 1
    assert len(conversations[0][2].message_pieces) == 1


def test_group_message_pieces_into_conversations_multiple_pieces_same_sequence_role():
    """Test grouping when multiple pieces have the same sequence and role."""
    pieces = [
        # Two user pieces in sequence 0 (e.g., multimodal with text and image)
        MessagePiece(role="user", conversation_id="conv1", sequence=0, original_value="Text piece"),
        MessagePiece(role="user", conversation_id="conv1", sequence=0, original_value="Image piece"),
        # One assistant piece in sequence 1
        MessagePiece(role="assistant", conversation_id="conv1", sequence=1, original_value="Response"),
    ]

    conversations = group_message_pieces_into_conversations(pieces)

    assert len(conversations) == 1  # 1 conversation
    assert len(conversations[0]) == 2  # 2 sequences
    assert len(conversations[0][0].message_pieces) == 2  # Sequence 0 has 2 pieces (both user role)
    assert len(conversations[0][1].message_pieces) == 1  # Sequence 1 has 1 piece


def test_group_conversation_message_pieces(sample_conversations: MutableSequence[Message]):
    # Get pieces from the first conversation
    all_pieces: list[MessagePiece] = []
    for response in sample_conversations:
        if response.message_pieces[0].conversation_id == sample_conversations[0].message_pieces[0].conversation_id:
            pieces = response.flatten_to_message_pieces([response])
            all_pieces.extend(pieces)

    # Filter to get pieces from the same conversation

    groups = group_conversation_message_pieces_by_sequence(all_pieces)
    assert groups
    assert len(groups) >= 1
    assert groups[0].message_pieces[0].sequence == 0


def test_group_conversation_message_pieces_multiple_groups(
    sample_conversations: MutableSequence[Message],
):
    # Get pieces from the first conversation
    all_pieces: list[MessagePiece] = []
    for response in sample_conversations:
        pieces = response.flatten_to_message_pieces([response])
        all_pieces.extend(pieces)

    # Filter to get pieces from the same conversation and add another piece
    if all_pieces:
        convo_group = [entry for entry in all_pieces if entry.conversation_id == all_pieces[0].conversation_id]
        convo_group.append(
            MessagePiece(
                role="assistant",
                original_value="Hello",
                conversation_id=convo_group[0].conversation_id,
                sequence=1,
            )
        )
        groups = group_conversation_message_pieces_by_sequence(convo_group)
        assert groups
        assert len(groups) >= 1


def test_message_piece_no_roles():
    with pytest.raises(ValueError, match="not a valid role."):
        Message(
            message_pieces=[
                MessagePiece(
                    role="",  # type: ignore
                    converted_value_data_type="text",
                    original_value="Hello",
                    converted_value="Hello",
                )
            ]
        )


@pytest.mark.asyncio
async def test_message_piece_sets_original_sha256():
    entry = MessagePiece(
        role="user",
        original_value="Hello",
    )

    entry.original_value = "newvalue"
    await entry.set_sha256_values_async()
    assert entry.original_value_sha256 == "70e01503173b8e904d53b40b3ebb3bded5e5d3add087d3463a4b1abe92f1a8ca"


@pytest.mark.asyncio
async def test_message_piece_sets_converted_sha256():
    entry = MessagePiece(
        role="user",
        original_value="Hello",
    )
    entry.converted_value = "newvalue"
    await entry.set_sha256_values_async()
    assert entry.converted_value_sha256 == "70e01503173b8e904d53b40b3ebb3bded5e5d3add087d3463a4b1abe92f1a8ca"


def test_order_message_pieces_by_conversation_single_conversation():
    pieces = [
        MessagePiece(
            role="user",
            id="prompt-1",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=datetime.now() - timedelta(seconds=10),
            sequence=2,
        ),
        MessagePiece(
            role="user",
            id="prompt-2",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=datetime.now() - timedelta(seconds=10),
            sequence=1,
        ),
        MessagePiece(
            role="user",
            id="prompt-3",
            original_value="Hello 3",
            conversation_id="conv1",
            timestamp=datetime.now(),
            sequence=3,
        ),
    ]

    expected = [
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=pieces[1].timestamp,
            sequence=1,
            id="prompt-2",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=pieces[0].timestamp,
            sequence=2,
            id="prompt-1",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv1",
            timestamp=pieces[2].timestamp,
            sequence=3,
            id="prompt-3",
        ),
    ]

    ordered = sort_message_pieces(pieces)
    assert ordered == expected


def test_order_message_pieces_by_conversation_multiple_conversations():
    pieces = [
        MessagePiece(
            role="user",
            original_value="Hello 4",
            conversation_id="conv2",
            timestamp=datetime.now() - timedelta(seconds=5),
            sequence=2,
            id="4",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=datetime.now() - timedelta(seconds=15),
            sequence=1,
            id="1",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv2",
            timestamp=datetime.now() - timedelta(seconds=10),
            sequence=1,
            id="3",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=datetime.now() - timedelta(seconds=10),
            sequence=2,
            id="2",
        ),
    ]

    expected = [
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=pieces[1].timestamp,
            sequence=1,
            id="1",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=pieces[3].timestamp,
            sequence=2,
            id="2",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv2",
            timestamp=pieces[2].timestamp,
            sequence=1,
            id="3",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 4",
            conversation_id="conv2",
            timestamp=pieces[0].timestamp,
            sequence=2,
            id="4",
        ),
    ]

    assert sort_message_pieces(pieces) == expected


def test_order_message_pieces_by_conversation_same_timestamp():
    timestamp = datetime.now()

    pieces = [
        MessagePiece(
            role="user",
            original_value="Hello 4",
            conversation_id="conv2",
            timestamp=timestamp,
            sequence=2,
            id="4",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=timestamp,
            sequence=1,
            id="1",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv2",
            timestamp=timestamp,
            sequence=1,
            id="3",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=timestamp,
            sequence=2,
            id="2",
        ),
    ]

    expected = [
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=pieces[1].timestamp,
            sequence=1,
            id="1",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=pieces[3].timestamp,
            sequence=2,
            id="2",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv2",
            timestamp=pieces[2].timestamp,
            sequence=1,
            id="3",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 4",
            conversation_id="conv2",
            timestamp=pieces[0].timestamp,
            sequence=2,
            id="4",
        ),
    ]

    sorted = sort_message_pieces(pieces)
    assert sorted == expected


def test_order_message_pieces_by_conversation_empty_list():
    pieces = []
    expected = []
    assert sort_message_pieces(pieces) == expected


def test_order_message_pieces_by_conversation_single_message():
    pieces = [MessagePiece(role="user", original_value="Hello 1", conversation_id="conv1", id="1")]
    expected = [MessagePiece(role="user", original_value="Hello 1", conversation_id="conv1", id="1")]

    assert sort_message_pieces(pieces) == expected


def test_order_message_pieces_by_conversation_same_timestamp_different_sequences():
    pieces = [
        MessagePiece(
            role="user", original_value="Hello 2", conversation_id="conv1", timestamp=datetime.now(), sequence=2, id="2"
        ),
        MessagePiece(
            role="user", original_value="Hello 1", conversation_id="conv1", timestamp=datetime.now(), sequence=1, id="1"
        ),
    ]
    for i, piece in enumerate(pieces):
        piece.prompt_id = f"prompt-{i}"
    expected = [
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=pieces[1].timestamp,
            sequence=1,
            id="1",
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=pieces[0].timestamp,
            sequence=2,
            id="2",
        ),
    ]

    assert sort_message_pieces(pieces) == expected


def test_message_piece_to_dict():
    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
        conversation_id="test_conversation",
        sequence=1,
        labels={"label1": "value1"},
        targeted_harm_categories=["violence", "illegal"],
        prompt_metadata={"key": "metadata"},
        converter_identifiers=[
            {"__type__": "Base64Converter", "__module__": "pyrit.prompt_converter.base64_converter"}
        ],
        prompt_target_identifier={"__type__": "MockPromptTarget", "__module__": "unit.mocks"},
        attack_identifier={
            "id": str(uuid.uuid4()),
            "__type__": "PromptSendingAttack",
            "__module__": "pyrit.executor.attack.single_turn.prompt_sending_attack",
        },
        scorer_identifier=ScorerIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
            class_description="A test scorer",
            identifier_type="instance",
        ),
        original_value_data_type="text",
        converted_value_data_type="text",
        response_error="none",
        originator="undefined",
        original_prompt_id=uuid.uuid4(),
        timestamp=datetime.now(),
        scores=[
            Score(
                id=str(uuid.uuid4()),
                score_value="false",
                score_value_description="true false score",
                score_type="true_false",
                score_category=["Category1"],
                score_rationale="Rationale text",
                score_metadata={"key": "value"},
                scorer_class_identifier=ScorerIdentifier(
                    class_name="Scorer1",
                    class_module="pyrit.score",
                    class_description="",
                    identifier_type="instance",
                ),
                message_piece_id=str(uuid.uuid4()),
                timestamp=datetime.now(),
                objective="Task1",
            )
        ],
    )

    result = entry.to_dict()

    expected_keys = [
        "id",
        "role",
        "conversation_id",
        "sequence",
        "timestamp",
        "labels",
        "targeted_harm_categories",
        "prompt_metadata",
        "converter_identifiers",
        "prompt_target_identifier",
        "attack_identifier",
        "scorer_identifier",
        "original_value_data_type",
        "original_value",
        "original_value_sha256",
        "converted_value_data_type",
        "converted_value",
        "converted_value_sha256",
        "response_error",
        "originator",
        "original_prompt_id",
        "scores",
    ]

    for key in expected_keys:
        assert key in result, f"Missing key: {key}"

    assert result["id"] == str(entry.id)
    assert result["role"] == entry._role
    assert result["conversation_id"] == entry.conversation_id
    assert result["sequence"] == entry.sequence
    assert result["timestamp"] == entry.timestamp.isoformat()
    assert result["labels"] == entry.labels
    assert result["targeted_harm_categories"] == entry.targeted_harm_categories
    assert result["prompt_metadata"] == entry.prompt_metadata
    assert result["converter_identifiers"] == entry.converter_identifiers
    assert result["prompt_target_identifier"] == entry.prompt_target_identifier
    assert result["attack_identifier"] == entry.attack_identifier
    assert result["scorer_identifier"] == entry.scorer_identifier.to_dict()
    assert result["original_value_data_type"] == entry.original_value_data_type
    assert result["original_value"] == entry.original_value
    assert result["original_value_sha256"] == entry.original_value_sha256
    assert result["converted_value_data_type"] == entry.converted_value_data_type
    assert result["converted_value"] == entry.converted_value
    assert result["converted_value_sha256"] == entry.converted_value_sha256
    assert result["response_error"] == entry.response_error
    assert result["originator"] == entry.originator
    assert result["original_prompt_id"] == str(entry.original_prompt_id)
    assert result["scores"] == [score.to_dict() for score in entry.scores]


def test_message_piece_scorer_identifier_dict_backward_compatibility():
    """Test that passing a dict for scorer_identifier works with deprecation warning."""

    scorer_dict = {
        "class_name": "TestScorer",
        "class_module": "pyrit.score.test_scorer",
        "class_description": "A test scorer",
        "identifier_type": "instance",
    }

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        entry = MessagePiece(
            role="user",
            original_value="Hello",
            scorer_identifier=scorer_dict,
        )

        # Check that a deprecation warning was issued
        assert len(w) == 1
        assert "deprecated" in str(w[0].message).lower()
        assert "0.13.0" in str(w[0].message)

    # Check that scorer_identifier is now a ScorerIdentifier
    assert isinstance(entry.scorer_identifier, ScorerIdentifier)
    assert entry.scorer_identifier.class_name == "TestScorer"
    assert entry.scorer_identifier.class_module == "pyrit.score.test_scorer"


def test_message_piece_scorer_identifier_none_default():
    """Test that scorer_identifier defaults to None when not provided."""
    entry = MessagePiece(
        role="user",
        original_value="Hello",
    )

    assert entry.scorer_identifier is None


def test_message_piece_to_dict_scorer_identifier_none():
    """Test that to_dict() returns None for scorer_identifier when not set."""
    entry = MessagePiece(
        role="user",
        original_value="Hello",
    )

    result = entry.to_dict()
    assert result["scorer_identifier"] is None


def test_construct_response_from_request_combines_metadata():
    # Create a message piece with metadata
    request = MessagePiece(
        role="user", original_value="test prompt", conversation_id="123", prompt_metadata={"key1": "value1", "key2": 2}
    )

    additional_metadata = {"key2": 3, "key3": "value3"}

    response = construct_response_from_request(
        request=request, response_text_pieces=["test response"], prompt_metadata=additional_metadata
    )

    assert len(response.message_pieces) == 1
    response_piece = response.message_pieces[0]

    assert response_piece.prompt_metadata["key1"] == "value1"  # Original value preserved
    assert response_piece.prompt_metadata["key2"] == 3  # Overridden by additional metadata
    assert response_piece.prompt_metadata["key3"] == "value3"  # Added from additional metadata

    assert response_piece.api_role == "assistant"
    assert response_piece.original_value == "test response"
    assert response_piece.conversation_id == "123"
    assert response_piece.original_value_data_type == "text"
    assert response_piece.converted_value_data_type == "text"
    assert response_piece.response_error == "none"


def test_construct_response_from_request_no_metadata():
    request = MessagePiece(role="user", original_value="test prompt", conversation_id="123")

    response = construct_response_from_request(request=request, response_text_pieces=["test response"])

    assert len(response.message_pieces) == 1
    response_piece = response.message_pieces[0]

    assert not response_piece.prompt_metadata

    assert response_piece.api_role == "assistant"
    assert response_piece.original_value == "test response"
    assert response_piece.conversation_id == "123"
    assert response_piece.original_value_data_type == "text"
    assert response_piece.converted_value_data_type == "text"
    assert response_piece.response_error == "none"


@pytest.mark.parametrize(
    "response_error,expected_has_error",
    [
        ("none", False),
        ("blocked", True),
        ("processing", True),
        ("unknown", True),
        ("empty", True),
    ],
)
def test_message_piece_has_error(response_error, expected_has_error):
    entry = MessagePiece(
        role="assistant",
        original_value="Test response",
        response_error=response_error,
    )
    assert entry.has_error() == expected_has_error


@pytest.mark.parametrize(
    "response_error,expected_is_blocked",
    [
        ("none", False),
        ("blocked", True),
        ("processing", False),
        ("unknown", False),
        ("empty", False),
    ],
)
def test_message_piece_is_blocked(response_error, expected_is_blocked):
    entry = MessagePiece(
        role="assistant",
        original_value="Test response",
        response_error=response_error,
    )
    assert entry.is_blocked() == expected_is_blocked


def test_message_piece_has_error_and_is_blocked_consistency():
    # Test that is_blocked implies has_error
    blocked_entry = MessagePiece(
        role="assistant",
        original_value="Blocked response",
        response_error="blocked",
    )
    assert blocked_entry.is_blocked() is True
    assert blocked_entry.has_error() is True

    # Test that not all errors are blocks
    error_entry = MessagePiece(
        role="assistant",
        original_value="Error response",
        response_error="unknown",
    )
    assert error_entry.is_blocked() is False
    assert error_entry.has_error() is True

    # Test that no error means not blocked
    no_error_entry = MessagePiece(
        role="assistant",
        original_value="Success response",
        response_error="none",
    )
    assert no_error_entry.is_blocked() is False
    assert no_error_entry.has_error() is False


def test_message_piece_harm_categories_none():
    """Test that harm_categories defaults to None."""
    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
    )
    assert entry.targeted_harm_categories == []


def test_message_piece_harm_categories_single():
    """Test that harm_categories can be set to a single category."""
    entry = MessagePiece(
        role="user", original_value="Hello", converted_value="Hello", targeted_harm_categories=["violence"]
    )
    assert entry.targeted_harm_categories == ["violence"]


def test_message_piece_harm_categories_multiple():
    """Test that harm_categories can be set to multiple categories."""
    harm_categories = ["violence", "illegal", "hate_speech"]
    entry = MessagePiece(
        role="user", original_value="Hello", converted_value="Hello", targeted_harm_categories=harm_categories
    )
    assert entry.targeted_harm_categories == harm_categories


def test_message_piece_harm_categories_serialization():
    """Test that harm_categories is properly serialized in to_dict()."""
    harm_categories = ["violence", "illegal"]
    entry = MessagePiece(
        role="user", original_value="Hello", converted_value="Hello", targeted_harm_categories=harm_categories
    )

    result = entry.to_dict()
    assert "targeted_harm_categories" in result
    assert result["targeted_harm_categories"] == harm_categories


def test_message_piece_harm_categories_with_labels():
    """Test that harm_categories and labels can coexist."""
    harm_categories = ["violence", "illegal"]
    labels = {"operation": "test_op", "researcher": "alice"}

    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
        targeted_harm_categories=harm_categories,
        labels=labels,
    )

    assert entry.targeted_harm_categories == harm_categories
    assert entry.labels == labels

    result = entry.to_dict()
    assert result["targeted_harm_categories"] == harm_categories
    assert result["labels"] == labels


class TestSimulatedAssistantRole:
    """Tests for simulated_assistant role properties."""

    def test_api_role_returns_assistant_for_assistant(self):
        """Test that api_role returns 'assistant' for assistant role."""
        piece = MessagePiece(role="assistant", original_value="Hello")
        assert piece.api_role == "assistant"

    def test_api_role_returns_assistant_for_simulated_assistant(self):
        """Test that api_role returns 'assistant' for simulated_assistant role."""
        piece = MessagePiece(role="simulated_assistant", original_value="Hello")
        assert piece.api_role == "assistant"

    def test_api_role_returns_user_for_user(self):
        """Test that api_role returns 'user' for user role."""
        piece = MessagePiece(role="user", original_value="Hello")
        assert piece.api_role == "user"

    def test_api_role_returns_system_for_system(self):
        """Test that api_role returns 'system' for system role."""
        piece = MessagePiece(role="system", original_value="Hello")
        assert piece.api_role == "system"

    def test_is_simulated_true_for_simulated_assistant(self):
        """Test that is_simulated returns True for simulated_assistant."""
        piece = MessagePiece(role="simulated_assistant", original_value="Hello")
        assert piece.is_simulated is True

    def test_is_simulated_false_for_assistant(self):
        """Test that is_simulated returns False for assistant."""
        piece = MessagePiece(role="assistant", original_value="Hello")
        assert piece.is_simulated is False

    def test_is_simulated_false_for_user(self):
        """Test that is_simulated returns False for user."""
        piece = MessagePiece(role="user", original_value="Hello")
        assert piece.is_simulated is False

    def test_get_role_for_storage_returns_simulated_assistant(self):
        """Test that get_role_for_storage returns the actual stored role."""
        piece = MessagePiece(role="simulated_assistant", original_value="Hello")
        assert piece.get_role_for_storage() == "simulated_assistant"

    def test_get_role_for_storage_returns_assistant(self):
        """Test that get_role_for_storage returns assistant for assistant role."""
        piece = MessagePiece(role="assistant", original_value="Hello")
        assert piece.get_role_for_storage() == "assistant"

    def test_get_role_for_storage_returns_user(self):
        """Test that get_role_for_storage returns user for user role."""
        piece = MessagePiece(role="user", original_value="Hello")
        assert piece.get_role_for_storage() == "user"

    def test_role_setter_sets_simulated_assistant(self):
        """Test that role setter can set simulated_assistant."""
        piece = MessagePiece(role="assistant", original_value="Hello")
        piece.role = "simulated_assistant"
        assert piece.get_role_for_storage() == "simulated_assistant"
        assert piece.api_role == "assistant"
        assert piece.is_simulated is True
