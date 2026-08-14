# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import uuid

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.score import ContentScorable, MessageScorable, Scorable


def _stored_message(value: str = "stored response"):
    """Return a message memory will accept, so it needs a conversation id."""
    return MessagePiece(
        role="assistant",
        original_value=value,
        conversation_id=str(uuid.uuid4()),
    ).to_message()


@pytest.mark.parametrize(
    "scorable, field_name",
    [
        (MessageScorable(message_piece_ids=(uuid.uuid4(),)), "message_piece_ids"),
        (ContentScorable(value="hello"), "value"),
    ],
)
def test_scorable_is_frozen(scorable, field_name):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(scorable, field_name, "changed")


@pytest.mark.parametrize(
    "scorable",
    [
        MessageScorable(message_piece_ids=(uuid.uuid4(),)),
        ContentScorable(value="hello"),
    ],
)
def test_every_scorable_is_a_scorable(scorable):
    assert isinstance(scorable, Scorable)


def test_content_scorable_names_content_rather_than_a_message():
    """Loose content has no message behind it, so it must not answer the message contract."""
    scorable = ContentScorable(value="hello")

    assert isinstance(scorable, Scorable)
    assert not isinstance(scorable, MessageScorable)
    assert not hasattr(scorable, "resolve_message")


@pytest.mark.parametrize("field_name", ["role_filter", "skip_on_error_result"])
def test_content_scorable_rejects_message_filters(field_name):
    # Loose content has no role and no error state. Accepting a role filter here used to
    # score nothing at all, silently, because the adapted message is always role="user".
    with pytest.raises(TypeError):
        ContentScorable(value="hello", **{field_name: "assistant"})


def test_scorables_are_keyword_only():
    with pytest.raises(TypeError):
        ContentScorable("hello")  # type: ignore[misc]


class TestMessageScorable:
    def test_defaults(self):
        piece_id = uuid.uuid4()
        scorable = MessageScorable(message_piece_ids=(piece_id,))

        assert scorable.message_piece_ids == (piece_id,)
        assert scorable.role_filter is None
        assert scorable.skip_on_error_result is False

    def test_from_message_names_the_pieces_rather_than_carrying_them(self):
        """A scorable is a reference, so a message in hand becomes its piece ids."""
        message = _stored_message()

        scorable = MessageScorable.from_message(message, role_filter="assistant")

        assert scorable.message_piece_ids == (message.get_piece().id,)
        assert scorable.role_filter == "assistant"
        assert not hasattr(scorable, "message")

    def test_resolves_from_memory(self, sqlite_instance: MemoryInterface):
        stored = _stored_message()
        sqlite_instance.add_message_to_memory(request=stored)
        scorable = MessageScorable.from_message(stored)

        assert scorable.resolve_message(memory=sqlite_instance).get_value() == "stored response"

    def test_raises_when_nothing_is_in_memory(self, sqlite_instance: MemoryInterface):
        missing_id = uuid.uuid4()
        scorable = MessageScorable(message_piece_ids=(missing_id,))

        with pytest.raises(ValueError, match=f"No message pieces found in memory for ids \\['{missing_id}'\\]"):
            scorable.resolve_message(memory=sqlite_instance)

    def test_raises_naming_only_the_missing_ids(self, sqlite_instance: MemoryInterface):
        """A partial resolution is a caller error, and the error must point at the bad ids."""
        stored = _stored_message()
        sqlite_instance.add_message_to_memory(request=stored)
        missing_id = uuid.uuid4()
        scorable = MessageScorable(message_piece_ids=(stored.get_piece().id, missing_id))

        with pytest.raises(ValueError, match=f"No message pieces found in memory for ids \\['{missing_id}'\\]"):
            scorable.resolve_message(memory=sqlite_instance)

    def test_raises_when_pieces_span_more_than_one_message(self, sqlite_instance: MemoryInterface):
        conversation_id = str(uuid.uuid4())
        first = MessagePiece(
            role="user", original_value="ask", conversation_id=conversation_id, sequence=0
        ).to_message()
        second = MessagePiece(
            role="assistant", original_value="answer", conversation_id=conversation_id, sequence=1
        ).to_message()
        sqlite_instance.add_message_to_memory(request=first)
        sqlite_instance.add_message_to_memory(request=second)
        scorable = MessageScorable(message_piece_ids=(first.get_piece().id, second.get_piece().id))

        with pytest.raises(ValueError, match="exactly one message"):
            scorable.resolve_message(memory=sqlite_instance)


class TestContentScorable:
    def test_defaults_to_text(self):
        assert ContentScorable(value="hello").data_type == "text"

    def test_adapts_to_an_unpersisted_message(self):
        """Loose scoring must stay usable with no memory behind it."""
        message = ContentScorable(value="loose text").to_ephemeral_message()

        piece = message.get_piece()
        assert piece.original_value == "loose text"
        assert piece.role == "user"
        assert piece.not_in_memory is True

    def test_adapts_non_text_data_types(self):
        message = ContentScorable(value="path/to.png", data_type="image_path").to_ephemeral_message()

        assert message.get_piece().original_value_data_type == "image_path"


class TestFromMessage:
    """A caller holding a persisted Message names its pieces rather than carrying it."""

    def test_persisted_message_becomes_a_reference(self):
        message = _stored_message()

        scorable = MessageScorable.from_message(message)

        assert isinstance(scorable, MessageScorable)
        assert scorable.message_piece_ids == (message.get_piece().id,)

    def test_filters_travel_onto_the_reference(self):
        scorable = MessageScorable.from_message(_stored_message(), role_filter="assistant", skip_on_error_result=True)

        assert isinstance(scorable, MessageScorable)
        assert scorable.role_filter == "assistant"
        assert scorable.skip_on_error_result is True


def test_resolution_preserves_the_order_the_scorable_names(sqlite_instance: MemoryInterface):
    """Memory returns its own order, but a multi-piece message reads differently if shuffled."""
    conversation_id = str(uuid.uuid4())
    first = MessagePiece(role="assistant", original_value="one", conversation_id=conversation_id, sequence=0)
    second = MessagePiece(role="assistant", original_value="two", conversation_id=conversation_id, sequence=0)
    sqlite_instance.add_message_to_memory(request=Message(message_pieces=[first, second]))

    reversed_scorable = MessageScorable(message_piece_ids=(second.id, first.id))

    resolved = reversed_scorable.resolve_message(memory=sqlite_instance)
    assert [piece.original_value for piece in resolved.message_pieces] == ["two", "one"]
