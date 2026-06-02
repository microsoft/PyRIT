# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
import uuid
import warnings
from collections.abc import MutableSequence
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from unit.mocks import MockPromptTarget, get_mock_target, get_sample_conversations

from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import (
    ComponentIdentifier,
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
    fake_now = datetime(2099, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    with patch("pyrit.models.messages.message_piece.datetime") as mock_datetime:
        mock_datetime.now.return_value = fake_now
        entry = MessagePiece(
            role="user",
            original_value="Hello",
            converted_value="Hello",
        )
    assert entry.timestamp == fake_now
    mock_datetime.now.assert_called_once_with(tz=timezone.utc)


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

    assert converter.class_name == "Base64Converter"
    assert converter.class_module == "pyrit.prompt_converter.base64_converter"


def test_prompt_targets_serialize(patch_central_database):
    target = MockPromptTarget()
    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
        prompt_target_identifier=target.get_identifier(),
    )
    assert patch_central_database.called
    assert entry.prompt_target_identifier.class_name == "MockPromptTarget"
    assert entry.prompt_target_identifier.class_module == "unit.mocks"


def test_executors_serialize():
    attack = PromptSendingAttack(objective_target=get_mock_target())

    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
        attack_identifier=attack.get_identifier(),
    )

    assert entry.attack_identifier.hash is not None
    assert entry.attack_identifier.class_name == "PromptSendingAttack"
    assert entry.attack_identifier.class_module == "pyrit.executor.attack.single_turn.prompt_sending"


async def test_hashes_generated():
    entry = MessagePiece(
        role="user",
        original_value="Hello1",
        converted_value="Hello2",
    )
    await entry.set_sha256_values_async()
    assert entry.original_value_sha256 == "948edbe7ede5aa7423476ae29dcd7d61e7711a071aea0d83698377effa896525"
    assert entry.converted_value_sha256 == "be98c2510e417405647facb89399582fc499c3de4452b3014857f92e6baad9a9"


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
    # Pydantic's literal validator rejects bad data types at construction time.
    with pytest.raises(ValueError, match="Input should be"):
        MessagePiece(
            role="user",
            original_value="Hello1",
            original_value_data_type="new_unknown_type",  # type: ignore[arg-type]
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
    # Pydantic's literal validator rejects bad roles at construction time.
    with pytest.raises(ValueError, match="Input should be"):
        Message(
            message_pieces=[
                MessagePiece(
                    role="",  # type: ignore[arg-type]
                    converted_value_data_type="text",
                    original_value="Hello",
                    converted_value="Hello",
                )
            ]
        )


async def test_message_piece_sets_original_sha256():
    entry = MessagePiece(
        role="user",
        original_value="Hello",
    )

    entry.original_value = "newvalue"
    await entry.set_sha256_values_async()
    assert entry.original_value_sha256 == "70e01503173b8e904d53b40b3ebb3bded5e5d3add087d3463a4b1abe92f1a8ca"


async def test_message_piece_sets_converted_sha256():
    entry = MessagePiece(
        role="user",
        original_value="Hello",
    )
    entry.converted_value = "newvalue"
    await entry.set_sha256_values_async()
    assert entry.converted_value_sha256 == "70e01503173b8e904d53b40b3ebb3bded5e5d3add087d3463a4b1abe92f1a8ca"


def test_order_message_pieces_by_conversation_single_conversation():
    id1, id2, id3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pieces = [
        MessagePiece(
            role="user",
            id=id1,
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=datetime.now(tz=timezone.utc) - timedelta(seconds=10),
            sequence=2,
        ),
        MessagePiece(
            role="user",
            id=id2,
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=datetime.now(tz=timezone.utc) - timedelta(seconds=10),
            sequence=1,
        ),
        MessagePiece(
            role="user",
            id=id3,
            original_value="Hello 3",
            conversation_id="conv1",
            timestamp=datetime.now(tz=timezone.utc),
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
            id=id2,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=pieces[0].timestamp,
            sequence=2,
            id=id1,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv1",
            timestamp=pieces[2].timestamp,
            sequence=3,
            id=id3,
        ),
    ]

    ordered = sort_message_pieces(pieces)
    assert ordered == expected


def test_order_message_pieces_by_conversation_multiple_conversations():
    id1, id2, id3, id4 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pieces = [
        MessagePiece(
            role="user",
            original_value="Hello 4",
            conversation_id="conv2",
            timestamp=datetime.now(tz=timezone.utc) - timedelta(seconds=5),
            sequence=2,
            id=id4,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=datetime.now(tz=timezone.utc) - timedelta(seconds=15),
            sequence=1,
            id=id1,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv2",
            timestamp=datetime.now(tz=timezone.utc) - timedelta(seconds=10),
            sequence=1,
            id=id3,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=datetime.now(tz=timezone.utc) - timedelta(seconds=10),
            sequence=2,
            id=id2,
        ),
    ]

    expected = [
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=pieces[1].timestamp,
            sequence=1,
            id=id1,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=pieces[3].timestamp,
            sequence=2,
            id=id2,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv2",
            timestamp=pieces[2].timestamp,
            sequence=1,
            id=id3,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 4",
            conversation_id="conv2",
            timestamp=pieces[0].timestamp,
            sequence=2,
            id=id4,
        ),
    ]

    assert sort_message_pieces(pieces) == expected


def test_order_message_pieces_by_conversation_same_timestamp():
    timestamp = datetime.now(tz=timezone.utc)
    id1, id2, id3, id4 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    pieces = [
        MessagePiece(
            role="user",
            original_value="Hello 4",
            conversation_id="conv2",
            timestamp=timestamp,
            sequence=2,
            id=id4,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=timestamp,
            sequence=1,
            id=id1,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv2",
            timestamp=timestamp,
            sequence=1,
            id=id3,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=timestamp,
            sequence=2,
            id=id2,
        ),
    ]

    expected = [
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=pieces[1].timestamp,
            sequence=1,
            id=id1,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=pieces[3].timestamp,
            sequence=2,
            id=id2,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 3",
            conversation_id="conv2",
            timestamp=pieces[2].timestamp,
            sequence=1,
            id=id3,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 4",
            conversation_id="conv2",
            timestamp=pieces[0].timestamp,
            sequence=2,
            id=id4,
        ),
    ]

    sorted_pieces = sort_message_pieces(pieces)
    assert sorted_pieces == expected


def test_order_message_pieces_by_conversation_empty_list():
    pieces = []
    expected = []
    assert sort_message_pieces(pieces) == expected


def test_order_message_pieces_by_conversation_single_message():
    only_id = uuid.uuid4()
    pieces = [MessagePiece(role="user", original_value="Hello 1", conversation_id="conv1", id=only_id)]
    expected = [
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            id=only_id,
            timestamp=pieces[0].timestamp,
        )
    ]

    assert sort_message_pieces(pieces) == expected


def test_order_message_pieces_by_conversation_same_timestamp_different_sequences():
    id1, id2 = uuid.uuid4(), uuid.uuid4()
    pieces = [
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=datetime.now(tz=timezone.utc),
            sequence=2,
            id=id2,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=datetime.now(tz=timezone.utc),
            sequence=1,
            id=id1,
        ),
    ]
    expected = [
        MessagePiece(
            role="user",
            original_value="Hello 1",
            conversation_id="conv1",
            timestamp=pieces[1].timestamp,
            sequence=1,
            id=id1,
        ),
        MessagePiece(
            role="user",
            original_value="Hello 2",
            conversation_id="conv1",
            timestamp=pieces[0].timestamp,
            sequence=2,
            id=id2,
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
            ComponentIdentifier(
                class_name="Base64Converter",
                class_module="pyrit.prompt_converter.base64_converter",
                params={"supported_input_types": ["text"], "supported_output_types": ["text"]},
            )
        ],
        prompt_target_identifier=ComponentIdentifier(
            class_name="MockPromptTarget",
            class_module="unit.mocks",
        ),
        attack_identifier=ComponentIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor.attack.single_turn.prompt_sending_attack",
        ),
        scorer_identifier=ComponentIdentifier(
            class_name="TestScorer",
            class_module="pyrit.score.test_scorer",
        ),
        original_value_data_type="text",
        converted_value_data_type="text",
        response_error="none",
        originator="undefined",
        original_prompt_id=uuid.uuid4(),
        timestamp=datetime.now(tz=timezone.utc),
        scores=[
            Score(
                id=str(uuid.uuid4()),
                score_value="false",
                score_value_description="true false score",
                score_type="true_false",
                score_category=["Category1"],
                score_rationale="Rationale text",
                score_metadata={"key": "value"},
                scorer_class_identifier=ComponentIdentifier(
                    class_name="Scorer1",
                    class_module="pyrit.score",
                ),
                message_piece_id=str(uuid.uuid4()),
                timestamp=datetime.now(tz=timezone.utc),
                objective="Task1",
            )
        ],
    )

    result = entry.model_dump(mode="json")

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
    assert result["role"] == entry.role
    assert result["conversation_id"] == entry.conversation_id
    assert result["sequence"] == entry.sequence
    # Pydantic v2 serializes UTC datetimes with a trailing "Z" rather than "+00:00".
    assert result["timestamp"] == entry.timestamp.isoformat().replace("+00:00", "Z")
    assert result["labels"] == entry.labels
    assert result["targeted_harm_categories"] == entry.targeted_harm_categories
    assert result["prompt_metadata"] == entry.prompt_metadata
    assert result["converter_identifiers"] == [conv.to_dict() for conv in entry.converter_identifiers]
    assert result["prompt_target_identifier"] == entry.prompt_target_identifier.to_dict()
    assert result["attack_identifier"] == entry.attack_identifier.to_dict()
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

    result = entry.model_dump(mode="json")
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

    result = entry.model_dump(mode="json")
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

    result = entry.model_dump(mode="json")
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
        """Test that role attribute returns the actual stored role."""
        piece = MessagePiece(role="simulated_assistant", original_value="Hello")
        assert piece.role == "simulated_assistant"

    def test_get_role_for_storage_returns_assistant(self):
        """Test that role attribute returns assistant for assistant role."""
        piece = MessagePiece(role="assistant", original_value="Hello")
        assert piece.role == "assistant"

    def test_get_role_for_storage_returns_user(self):
        """Test that role attribute returns user for user role."""
        piece = MessagePiece(role="user", original_value="Hello")
        assert piece.role == "user"

    def test_role_setter_sets_simulated_assistant(self):
        """Test that role setter can set simulated_assistant."""
        piece = MessagePiece(role="assistant", original_value="Hello")
        piece.role = "simulated_assistant"
        assert piece.role == "simulated_assistant"
        assert piece.api_role == "assistant"
        assert piece.is_simulated is True


def test_set_piece_not_in_memory_sets_flag():
    entry = MessagePiece(
        role="user",
        original_value="Hello",
        converted_value="Hello",
    )
    original_id = entry.id
    assert entry.id is not None
    assert entry.not_in_memory is False
    entry.not_in_memory = True
    assert entry.not_in_memory is True
    # id is preserved so scorers can still reference the piece within the in-memory call
    assert entry.id == original_id


def test_to_dict_from_dict_roundtrip():
    from datetime import datetime, timezone

    scorer_id = ComponentIdentifier(
        class_name="SelfAskTrueFalseScorer",
        class_module="pyrit.score",
    )
    target_id = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://api.example.com"},
    )
    attack_id = ComponentIdentifier(
        class_name="PromptSendingAttack",
        class_module="pyrit.executor.attack",
    )
    converter_id = ComponentIdentifier(
        class_name="Base64Converter",
        class_module="pyrit.prompt_converter",
    )
    score = Score(
        score_value="true",
        score_value_description="met objective",
        score_type="true_false",
        score_rationale="clearly met",
        scorer_class_identifier=scorer_id,
        message_piece_id="mp-score-ref",
        timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )
    original = MessagePiece(
        id="12345678-aaaa-bbbb-cccc-000000000001",
        role="assistant",
        original_value="Hello world",
        original_value_sha256="abc123",
        converted_value="SGVsbG8gd29ybGQ=",
        converted_value_sha256="def456",
        conversation_id="conv-1",
        sequence=2,
        timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        prompt_metadata={"doc_type": "text"},
        converter_identifiers=[converter_id],
        prompt_target_identifier=target_id,
        attack_identifier=attack_id,
        original_value_data_type="text",
        converted_value_data_type="text",
        response_error="none",
        original_prompt_id=uuid.UUID("12345678-1234-1234-1234-123456789abc"),
    )
    roundtripped = MessagePiece.model_validate(original.model_dump(mode="json"))
    assert original.model_dump(mode="json") == roundtripped.model_dump(mode="json")


def test_to_dict_from_dict_roundtrip_after_set_piece_not_in_memory():
    """Pieces marked not-in-memory keep their id; the flag itself is not serialized."""
    piece = MessagePiece(
        role="user",
        original_value="Hello world",
        conversation_id="conv-not-in-db",
    )
    original_id = piece.id
    piece.not_in_memory = True
    assert piece.not_in_memory is True
    assert piece.id == original_id

    serialized = piece.model_dump(mode="json")
    # The not_in_memory field is intentionally excluded from serialization.
    assert "not_in_memory" not in serialized
    assert serialized["id"] == str(original_id)

    roundtripped = MessagePiece.model_validate(serialized)
    assert isinstance(roundtripped.id, uuid.UUID)
    assert roundtripped.id == original_id
    # Flag does not survive serialization (in-process only).
    assert roundtripped.not_in_memory is False


class TestCopyLineageFrom:
    def _make_piece(self, **overrides) -> MessagePiece:
        defaults = {
            "role": "user",
            "original_value": "hello",
            "conversation_id": "conv-source",
        }
        defaults.update(overrides)
        return MessagePiece(**defaults)

    def test_copies_lineage_fields_from_source_to_target(self) -> None:
        source = self._make_piece(
            conversation_id="conv-A",
            attack_identifier={"__type__": "Attack", "__module__": "x", "id": "atk-1"},
            prompt_target_identifier={"__type__": "Target", "__module__": "x", "id": "tgt-1"},
        )
        source.prompt_metadata = {"k": "v"}

        target = self._make_piece(conversation_id="conv-B", role="assistant", original_value="hi")

        target.copy_lineage_from(source=source)

        assert target.conversation_id == "conv-A"
        assert target.attack_identifier == source.attack_identifier
        assert target.prompt_target_identifier == source.prompt_target_identifier
        assert target.prompt_metadata == {"k": "v"}

    def test_labels_and_metadata_are_shallow_copied(self) -> None:
        source = self._make_piece()
        source.prompt_metadata = {"meta": "1"}

        target = self._make_piece(role="assistant")

        target.copy_lineage_from(source=source)

        # Mutating the target containers should not affect the source.
        target.prompt_metadata["meta"] = "2"
        assert source.prompt_metadata == {"meta": "1"}

    def test_non_lineage_fields_are_preserved(self) -> None:
        source = self._make_piece(conversation_id="conv-A")
        target = self._make_piece(
            role="assistant",
            original_value="target-value",
            conversation_id="conv-B",
        )
        original_value_before = target.original_value
        role_before = target.role
        id_before = target.id

        target.copy_lineage_from(source=source)

        assert target.original_value == original_value_before
        assert target.role == role_before
        assert target.id == id_before


class TestPhase3PydanticMigration:
    """Phase 3 §F.2 sanity tests for the MessagePiece Pydantic migration."""

    def test_to_dict_golden_shape(self) -> None:
        ts = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        piece_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        conv_id = "conv-123"
        piece = MessagePiece(
            id=piece_id,
            role="user",
            conversation_id=conv_id,
            sequence=2,
            timestamp=ts,
            original_value="hello",
            converted_value="hello",
        )

        d = piece.model_dump(mode="json")

        expected_keys = [
            "id",
            "role",
            "conversation_id",
            "sequence",
            "timestamp",
            "original_value",
            "original_value_data_type",
            "original_value_sha256",
            "converted_value",
            "converted_value_data_type",
            "converted_value_sha256",
            "response_error",
            "originator",
            "original_prompt_id",
            "labels",
            "targeted_harm_categories",
            "prompt_metadata",
            "converter_identifiers",
            "prompt_target_identifier",
            "attack_identifier",
            "scorer_identifier",
            "scores",
        ]
        assert list(d.keys()) == expected_keys
        assert d["id"] == str(piece_id)
        assert d["role"] == "user"
        assert d["conversation_id"] == conv_id
        assert d["sequence"] == 2
        assert d["timestamp"] == ts.isoformat().replace("+00:00", "Z")
        assert d["labels"] == {}
        assert d["targeted_harm_categories"] == []
        assert d["prompt_metadata"] == {}
        assert d["converter_identifiers"] == []
        assert d["prompt_target_identifier"] is None
        assert d["attack_identifier"] is None
        assert d["scorer_identifier"] is None
        assert d["original_value_data_type"] == "text"
        assert d["original_value"] == "hello"
        assert d["converted_value_data_type"] == "text"
        assert d["converted_value"] == "hello"
        assert d["response_error"] == "none"
        assert d["originator"] == "undefined"
        assert d["original_prompt_id"] == str(piece_id)
        assert d["scores"] == []

    def test_message_piece_is_unhashable(self) -> None:
        assert MessagePiece.__hash__ is None

        piece = MessagePiece(role="user", original_value="hello")
        with pytest.raises(TypeError):
            hash(piece)

    def test_unknown_kwarg_raises(self) -> None:
        with pytest.raises(Exception) as exc_info:
            MessagePiece(role="user", original_value="hello", typo_field="oops")
        assert "typo_field" in str(exc_info.value) or "Extra" in str(exc_info.value)


class TestMessagePieceDeprecationWarnings:
    """Tests for deprecation warnings on parameters scheduled for removal."""

    def _emit_deprecation_msgs(self, **kwargs) -> list[warnings.WarningMessage]:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            MessagePiece(role="user", original_value="hello", **kwargs)
        return [x for x in w if issubclass(x.category, DeprecationWarning)]

    def test_scorer_identifier_emits_deprecation_warning(self):
        scorer_id = ComponentIdentifier(class_name="X", class_module="x")
        msgs = self._emit_deprecation_msgs(scorer_identifier=scorer_id)
        assert any("scorer_identifier" in str(m.message) for m in msgs)

    def test_scorer_identifier_omitted_no_warning(self):
        msgs = self._emit_deprecation_msgs()
        assert not any("scorer_identifier" in str(m.message) for m in msgs)

    def test_originator_non_default_emits_deprecation_warning(self):
        msgs = self._emit_deprecation_msgs(originator="attack")
        assert any("originator" in str(m.message) for m in msgs)

    def test_originator_default_no_warning(self):
        msgs = self._emit_deprecation_msgs(originator="undefined")
        assert not any("originator" in str(m.message) for m in msgs)

    def test_scores_emits_deprecation_warning(self):
        score = Score(
            score_value="true",
            score_value_description="d",
            score_type="true_false",
            score_rationale="r",
            scorer_class_identifier=ComponentIdentifier(class_name="S", class_module="s"),
            message_piece_id="mp-1",
        )
        msgs = self._emit_deprecation_msgs(scores=[score])
        assert any("scores" in str(m.message) for m in msgs)

    def test_scores_omitted_no_warning(self):
        msgs = self._emit_deprecation_msgs()
        assert not any("scores" in str(m.message) for m in msgs)

    def test_targeted_harm_categories_emits_deprecation_warning(self):
        msgs = self._emit_deprecation_msgs(targeted_harm_categories=["violence"])
        assert any("targeted_harm_categories" in str(m.message) for m in msgs)

    def test_targeted_harm_categories_omitted_no_warning(self):
        msgs = self._emit_deprecation_msgs()
        assert not any("targeted_harm_categories" in str(m.message) for m in msgs)

    def test_labels_emits_deprecation_warning(self):
        msgs = self._emit_deprecation_msgs(labels={"k": "v"})
        assert any("labels" in str(m.message) for m in msgs)

    def test_labels_omitted_no_warning(self):
        msgs = self._emit_deprecation_msgs()
        assert not any("labels" in str(m.message) for m in msgs)

    def test_memory_load_roundtrip_does_not_emit_deprecation_warnings(self) -> None:
        """Reconstructing a MessagePiece from PromptMemoryEntry must not emit deprecations.

        The memory-layer load path assigns deprecated containers (``labels``,
        ``scores``, ``targeted_harm_categories``) post-construction so the
        deprecation-kwarg validator is not triggered. This regression-guards
        that pattern.
        """
        from pyrit.memory.memory_models import PromptMemoryEntry

        piece = MessagePiece(
            role="user",
            original_value="hello",
            conversation_id="conv-deprec",
        )
        piece.labels = {"k": "v"}
        piece.targeted_harm_categories = ["violence"]

        entry = PromptMemoryEntry(entry=piece)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            reconstructed = entry.get_message_piece()

        deprecation_msgs = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert deprecation_msgs == [], [str(m.message) for m in deprecation_msgs]
        assert reconstructed.labels == {"k": "v"}
        assert reconstructed.targeted_harm_categories == ["violence"]


class TestMessagePieceDeprecatedMethodShims:
    """Tests for the deprecated method shims scheduled for removal in 0.16.0."""

    def test_to_dict_emits_warning_and_matches_model_dump(self) -> None:
        piece = MessagePiece(role="user", original_value="hello")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = piece.to_dict()
        msgs = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert any("to_dict" in str(m.message) for m in msgs)
        assert result == piece.model_dump(mode="json")

    def test_from_dict_emits_warning_and_matches_model_validate(self) -> None:
        piece = MessagePiece(role="user", original_value="hello")
        serialized = piece.model_dump(mode="json")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            reconstructed = MessagePiece.from_dict(serialized)
        msgs = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert any("from_dict" in str(m.message) for m in msgs)
        assert reconstructed.model_dump(mode="json") == serialized

    def test_set_piece_not_in_database_emits_warning_and_sets_flag(self) -> None:
        piece = MessagePiece(role="user", original_value="hello")
        assert piece.not_in_memory is False
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            piece.set_piece_not_in_database()
        msgs = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert any("set_piece_not_in_database" in str(m.message) for m in msgs)
        assert piece.not_in_memory is True
