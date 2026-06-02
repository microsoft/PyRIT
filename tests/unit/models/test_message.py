# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models import (
    Message,
    MessagePiece,
)


@pytest.fixture
def message_pieces() -> list[MessagePiece]:
    return [
        MessagePiece(
            role="user",
            original_value="First piece",
            conversation_id="test-conversation-1",
        ),
        MessagePiece(
            role="user",
            original_value="Second piece",
            conversation_id="test-conversation-1",
        ),
        MessagePiece(
            role="user",
            original_value="Third piece",
            conversation_id="test-conversation-1",
        ),
    ]


@pytest.fixture
def message(message_pieces) -> Message:
    return Message(message_pieces=message_pieces)


def test_get_piece_returns_correct_piece(message: Message) -> None:
    # Test getting first piece (default)
    first_piece = message.get_piece()
    assert first_piece.original_value == "First piece"
    assert first_piece.api_role == "user"

    # Test getting specific pieces by index
    second_piece = message.get_piece(1)
    assert second_piece.original_value == "Second piece"
    assert second_piece.api_role == "user"

    third_piece = message.get_piece(2)
    assert third_piece.original_value == "Third piece"
    assert third_piece.api_role == "user"


def test_get_piece_raises_index_error_for_invalid_index(message: Message) -> None:
    with pytest.raises(IndexError, match="No message piece at index 3"):
        message.get_piece(3)


def test_get_piece_raises_value_error_for_empty_request() -> None:
    with pytest.raises(ValueError, match="at least one message piece"):
        Message(message_pieces=[])


def test_get_pieces_by_type_returns_matching_pieces() -> None:
    conversation_id = "test-conv"
    text_piece = MessagePiece(
        role="user", original_value="hello", converted_value="hello", conversation_id=conversation_id
    )
    image_piece = MessagePiece(
        role="user",
        original_value="/img.png",
        converted_value="/img.png",
        converted_value_data_type="image_path",
        conversation_id=conversation_id,
    )
    msg = Message([text_piece, image_piece])

    result = msg.get_pieces_by_type(data_type="text")
    assert len(result) == 1
    assert result[0] is text_piece

    result = msg.get_pieces_by_type(data_type="image_path")
    assert len(result) == 1
    assert result[0] is image_piece


def test_get_pieces_by_type_returns_empty_for_no_match() -> None:
    piece = MessagePiece(role="user", original_value="hello", converted_value="hello")
    msg = Message([piece])
    assert msg.get_pieces_by_type(data_type="image_path") == []


def test_get_piece_by_type_returns_first_match() -> None:
    conversation_id = "test-conv"
    text1 = MessagePiece(role="user", original_value="a", converted_value="a", conversation_id=conversation_id)
    text2 = MessagePiece(role="user", original_value="b", converted_value="b", conversation_id=conversation_id)
    msg = Message([text1, text2])
    assert msg.get_piece_by_type(data_type="text") is text1


def test_get_piece_by_type_returns_none_for_no_match() -> None:
    piece = MessagePiece(role="user", original_value="hello", converted_value="hello")
    msg = Message([piece])
    assert msg.get_piece_by_type(data_type="image_path") is None


def test_get_all_values_returns_all_converted_strings(message_pieces: list[MessagePiece]) -> None:
    response_one = Message(message_pieces=message_pieces[:2])
    response_two = Message(message_pieces=message_pieces[2:])

    flattened = Message.get_all_values([response_one, response_two])

    assert flattened == ["First piece", "Second piece", "Third piece"]


class TestMessageDuplication:
    """Tests for the Message.duplicate_message() method."""

    def test_duplicate_message_creates_new_ids(self, message: Message) -> None:
        """Test that duplicate_message creates new IDs for all pieces."""
        original_ids = [piece.id for piece in message.message_pieces]

        duplicated = message.duplicate_message()

        duplicated_ids = [piece.id for piece in duplicated.message_pieces]

        # Verify new IDs are different from original
        for orig_id, dup_id in zip(original_ids, duplicated_ids, strict=False):
            assert orig_id != dup_id

        # Verify duplicated IDs are unique
        assert len(set(duplicated_ids)) == len(duplicated_ids)

    def test_duplicate_message_preserves_content(self, message: Message) -> None:
        """Test that duplicate_message preserves all content fields."""
        duplicated = message.duplicate_message()

        for orig_piece, dup_piece in zip(message.message_pieces, duplicated.message_pieces, strict=False):
            assert orig_piece.original_value == dup_piece.original_value
            assert orig_piece.converted_value == dup_piece.converted_value
            assert orig_piece.api_role == dup_piece.api_role
            assert orig_piece.is_simulated == dup_piece.is_simulated
            assert orig_piece.conversation_id == dup_piece.conversation_id
            assert orig_piece.sequence == dup_piece.sequence

    def test_duplicate_message_preserves_original_prompt_id(self, message: Message) -> None:
        """Test that duplicate_message preserves original_prompt_id for tracing."""
        duplicated = message.duplicate_message()

        for orig_piece, dup_piece in zip(message.message_pieces, duplicated.message_pieces, strict=False):
            assert orig_piece.original_prompt_id == dup_piece.original_prompt_id

    def test_duplicate_message_creates_new_timestamp(self, message: Message) -> None:
        """Test that duplicate_message creates new timestamps."""
        import time

        original_timestamps = [piece.timestamp for piece in message.message_pieces]

        time.sleep(0.01)  # Small delay to ensure different timestamp
        duplicated = message.duplicate_message()

        for dup_piece in duplicated.message_pieces:
            # Verify timestamp is newer than all original timestamps
            for orig_ts in original_timestamps:
                assert dup_piece.timestamp >= orig_ts

    def test_duplicate_message_is_deep_copy(self, message: Message) -> None:
        """Test that duplicate_message creates a deep copy (modifications don't affect original)."""
        duplicated = message.duplicate_message()

        # Modify the duplicated message
        duplicated.message_pieces[0].original_value = "Modified value"

        # Verify original is unchanged
        assert message.message_pieces[0].original_value == "First piece"

    def test_duplicate_message_multiple_times(self, message: Message) -> None:
        """Test that duplicating multiple times creates unique IDs each time."""
        dup1 = message.duplicate_message()
        dup2 = message.duplicate_message()

        dup1_ids = {piece.id for piece in dup1.message_pieces}
        dup2_ids = {piece.id for piece in dup2.message_pieces}

        # Verify no overlap between duplicates
        assert dup1_ids.isdisjoint(dup2_ids)


class TestMessageFromPrompt:
    """Tests for the Message.from_prompt() class method."""

    def test_from_prompt_creates_user_message(self) -> None:
        """Test that from_prompt creates a valid user message."""
        message = Message.from_prompt(prompt="Hello world", role="user")

        assert len(message.message_pieces) == 1
        assert message.message_pieces[0].original_value == "Hello world"
        assert message.message_pieces[0].converted_value == "Hello world"
        assert message.message_pieces[0].api_role == "user"

    def test_from_prompt_creates_assistant_message(self) -> None:
        """Test that from_prompt creates a valid assistant message."""
        message = Message.from_prompt(prompt="Response text", role="assistant")

        assert len(message.message_pieces) == 1
        assert message.message_pieces[0].api_role == "assistant"

    def test_from_system_prompt_creates_system_message(self) -> None:
        """Test that from_system_prompt creates a valid system message."""
        message = Message.from_system_prompt(system_prompt="You are a helpful assistant")

        assert len(message.message_pieces) == 1
        assert message.message_pieces[0].api_role == "system"
        assert message.message_pieces[0].original_value == "You are a helpful assistant"

    def test_from_prompt_with_empty_string(self) -> None:
        """Test that from_prompt works with empty string."""
        message = Message.from_prompt(prompt="", role="user")

        assert len(message.message_pieces) == 1
        assert message.message_pieces[0].original_value == ""


def test_message_to_dict() -> None:
    """Test that to_dict returns the expected dictionary structure."""
    message = Message.from_prompt(prompt="Hello world", role="user")
    result = message.to_dict()

    assert result["role"] == "user"
    assert result["converted_value"] == "Hello world"
    assert result["converted_value_data_type"] == "text"
    assert "conversation_id" in result
    assert "sequence" in result
    assert len(result["pieces"]) == 1
    assert result["pieces"][0]["converted_value"] == "Hello world"
    assert result["pieces"][0]["converted_value_data_type"] == "text"


class TestMessageSimulatedAssistantRole:
    """Tests for Message simulated_assistant role properties."""

    def test_api_role_returns_assistant_for_simulated_assistant(self) -> None:
        """Test that Message.api_role returns 'assistant' for simulated_assistant."""
        piece = MessagePiece(role="simulated_assistant", original_value="Hello", conversation_id="test")
        message = Message(message_pieces=[piece])
        assert message.api_role == "assistant"

    def test_api_role_returns_assistant_for_assistant(self) -> None:
        """Test that Message.api_role returns 'assistant' for assistant."""
        piece = MessagePiece(role="assistant", original_value="Hello", conversation_id="test")
        message = Message(message_pieces=[piece])
        assert message.api_role == "assistant"

    def test_is_simulated_true_for_simulated_assistant(self) -> None:
        """Test that Message.is_simulated returns True for simulated_assistant."""
        piece = MessagePiece(role="simulated_assistant", original_value="Hello", conversation_id="test")
        message = Message(message_pieces=[piece])
        assert message.is_simulated is True

    def test_is_simulated_false_for_assistant(self) -> None:
        """Test that Message.is_simulated returns False for assistant."""
        piece = MessagePiece(role="assistant", original_value="Hello", conversation_id="test")
        message = Message(message_pieces=[piece])
        assert message.is_simulated is False

    def test_is_simulated_false_for_empty_pieces(self) -> None:
        """Test that Message.is_simulated returns False for empty pieces (via skip_validation)."""
        message = Message(message_pieces=[MessagePiece(role="user", original_value="x", conversation_id="test")])
        message.message_pieces = []  # Manually empty for edge case test
        assert message.is_simulated is False

    def test_set_simulated_role_sets_all_pieces(self) -> None:
        """Test that set_simulated_role sets assistant pieces to simulated_assistant."""
        pieces = [
            MessagePiece(role="assistant", original_value="Hello", conversation_id="test"),
            MessagePiece(role="assistant", original_value="World", conversation_id="test"),
        ]
        message = Message(message_pieces=pieces)

        assert message.is_simulated is False
        assert message.api_role == "assistant"

        message.set_simulated_role()

        assert message.is_simulated is True
        assert message.api_role == "assistant"
        for piece in message.message_pieces:
            assert piece.role == "simulated_assistant"
            assert piece.is_simulated is True

    def test_set_simulated_role_only_changes_assistant_role(self) -> None:
        """Test that set_simulated_role only changes assistant roles, not other roles."""
        pieces = [
            MessagePiece(role="user", original_value="Hello", conversation_id="test"),
            MessagePiece(role="user", original_value="World", conversation_id="test"),
        ]
        message = Message(message_pieces=pieces)

        message.set_simulated_role()

        # User roles should remain unchanged
        for piece in message.message_pieces:
            assert piece.role == "user"
            assert piece.is_simulated is False


def test_to_dict_from_dict_roundtrip():
    from datetime import datetime, timezone

    pieces = [
        MessagePiece(
            role="user",
            original_value="What is the capital of France?",
            conversation_id="conv-rt",
            sequence=0,
            timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        ),
        MessagePiece(
            role="user",
            original_value="image_link.png",
            original_value_data_type="image_path",
            conversation_id="conv-rt",
            sequence=0,
            timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        ),
    ]
    original = Message(message_pieces=pieces)
    roundtripped = Message.from_dict(original.to_dict())
    assert original.to_dict() == roundtripped.to_dict()


class TestSetResponseNotInMemory:
    """Tests for ``Message.set_response_not_in_memory`` and its deprecation shim."""

    def test_set_response_not_in_memory_flags_every_piece(self) -> None:
        pieces = [
            MessagePiece(role="user", original_value="a", conversation_id="conv-1"),
            MessagePiece(role="user", original_value="b", conversation_id="conv-1"),
        ]
        message = Message(message_pieces=pieces)
        for p in pieces:
            assert p.not_in_memory is False
        message.set_response_not_in_memory()
        for p in pieces:
            assert p.not_in_memory is True

    def test_set_response_not_in_database_emits_warning_and_delegates(self) -> None:
        import warnings as _warnings

        piece = MessagePiece(role="user", original_value="hello")
        message = Message(message_pieces=[piece])
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            message.set_response_not_in_database()
        msgs = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert any("set_response_not_in_database" in str(m.message) for m in msgs)
        assert piece.not_in_memory is True


class TestMessagePydanticShape:
    """Tests for the Pydantic v2 BaseModel behavior of Message."""

    def test_keyword_construction_does_not_warn(self) -> None:
        import warnings as _warnings

        piece = MessagePiece(role="user", original_value="hi", conversation_id="c")
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            Message(message_pieces=[piece])
        assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]

    def test_positional_construction_warns_and_works(self) -> None:
        import warnings as _warnings

        piece = MessagePiece(role="user", original_value="hi", conversation_id="c")
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            message = Message([piece])
        assert message.message_pieces == [piece]
        assert any(issubclass(w.category, DeprecationWarning) and "positional" in str(w.message) for w in caught)

    def test_too_many_positional_args_raises(self) -> None:
        piece = MessagePiece(role="user", original_value="hi", conversation_id="c")
        with pytest.raises(TypeError, match="at most 1 positional argument"):
            Message([piece], [piece])

    def test_skip_validation_kwarg_is_deprecated_noop(self) -> None:
        import warnings as _warnings

        piece = MessagePiece(role="user", original_value="hi", conversation_id="c")
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            message = Message(message_pieces=[piece], skip_validation=True)
        assert message.message_pieces == [piece]
        assert any(issubclass(w.category, DeprecationWarning) and "skip_validation" in str(w.message) for w in caught)

    def test_model_validate_canonical_shape(self) -> None:
        piece = MessagePiece(role="user", original_value="hi", conversation_id="c")
        message = Message.model_validate({"message_pieces": [piece.model_dump()]})
        assert message.get_value() == "hi"

    def test_model_validate_legacy_dict_shape(self) -> None:
        original = Message.from_prompt(prompt="legacy hello", role="user")
        rebuilt = Message.model_validate(original.to_dict())
        assert rebuilt.get_value() == "legacy hello"

    def test_value_equality(self, message_pieces: list[MessagePiece]) -> None:
        assert Message(message_pieces=message_pieces) == Message(message_pieces=message_pieces)

    def test_membership_uses_value_equality(self, message_pieces: list[MessagePiece]) -> None:
        a = Message(message_pieces=message_pieces)
        b = Message(message_pieces=message_pieces)
        assert a in [b]

    def test_validate_instance_method_still_callable(self, message: Message) -> None:
        message.validate()
        message.message_pieces = []
        with pytest.raises(ValueError, match="at least one message piece"):
            message.validate()

    def test_duplicate_creates_new_ids_and_deep_copy(self, message: Message) -> None:
        duplicated = message.duplicate()
        original_ids = {p.id for p in message.message_pieces}
        duplicated_ids = {p.id for p in duplicated.message_pieces}
        assert original_ids.isdisjoint(duplicated_ids)
        duplicated.message_pieces[0].original_value = "changed"
        assert message.message_pieces[0].original_value == "First piece"

    def test_to_dict_keeps_legacy_keys_while_model_dump_is_canonical(self) -> None:
        message = Message.from_prompt(prompt="hi", role="user")
        with pytest.warns(DeprecationWarning):
            legacy = message.to_dict()
        assert set(legacy) == {
            "role",
            "converted_value",
            "conversation_id",
            "sequence",
            "converted_value_data_type",
            "pieces",
        }
        assert set(message.model_dump()) == {"message_pieces"}


class TestMessageModuleLayout:
    """Lock in the messages-package layout and its backward-compatible re-exports."""

    def test_conversation_helpers_live_in_conversations_module(self) -> None:
        from pyrit.models import messages
        from pyrit.models.messages import conversations

        for name in (
            "get_all_values",
            "flatten_to_message_pieces",
            "group_conversation_message_pieces_by_sequence",
            "group_message_pieces_into_conversations",
            "construct_response_from_request",
        ):
            assert getattr(conversations, name) is getattr(messages, name)

    def test_legacy_module_paths_reexport_same_objects(self) -> None:
        import pyrit.models.message as legacy_message
        import pyrit.models.message_piece as legacy_message_piece
        from pyrit.models.messages.message import Message as PackagedMessage
        from pyrit.models.messages.message_piece import MessagePiece as PackagedMessagePiece

        assert legacy_message.Message is PackagedMessage
        assert legacy_message_piece.MessagePiece is PackagedMessagePiece
