# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import uuid
from unittest.mock import MagicMock

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import MessagePiece
from pyrit.score import ContentScorable, MessageReferenceScorable, MessageScorable, Scorable, SingleMessageScorable


def _message():
    return MessagePiece(role="assistant", original_value="hello").to_message()


def _stored_message(value: str = "stored response"):
    """Return a message that memory will accept, so it needs a conversation id."""
    return MessagePiece(
        role="assistant",
        original_value=value,
        conversation_id=str(uuid.uuid4()),
    ).to_message()


def _no_memory() -> MemoryInterface:
    """Return a memory that the test can assert was never touched."""
    return MagicMock(spec=MemoryInterface)


@pytest.mark.parametrize(
    "scorable, field_name",
    [
        (MessageScorable(message=_message()), "message"),
        (MessageReferenceScorable(message_piece_ids=(uuid.uuid4(),)), "message_piece_ids"),
        (ContentScorable(value="hello"), "value"),
    ],
)
def test_scorable_is_frozen(scorable, field_name):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(scorable, field_name, "changed")


@pytest.mark.parametrize(
    "scorable",
    [
        MessageScorable(message=_message()),
        MessageReferenceScorable(message_piece_ids=(uuid.uuid4(),)),
    ],
)
def test_scorables_naming_a_message_share_one_base(scorable):
    assert isinstance(scorable, SingleMessageScorable)
    assert isinstance(scorable, Scorable)


def test_content_scorable_names_content_rather_than_a_message():
    """Loose content has no message behind it, so it must not inherit message identity."""
    scorable = ContentScorable(value="hello")

    assert isinstance(scorable, Scorable)
    assert not isinstance(scorable, SingleMessageScorable)


@pytest.mark.parametrize("field_name", ["role_filter", "skip_on_error_result"])
def test_content_scorable_rejects_message_filters(field_name):
    # Loose content has no role and no error state. Accepting a role filter here used to
    # score nothing at all, silently, because the adapted message is always role="user".
    with pytest.raises(TypeError):
        ContentScorable(value="hello", **{field_name: "assistant"})


def test_single_message_scorable_cannot_be_instantiated():
    # The base only declares the contract; a scorable must say how it resolves.
    assert "resolve_message" in SingleMessageScorable.__abstractmethods__

    with pytest.raises(TypeError):
        SingleMessageScorable()  # type: ignore[abstract]


def test_scorables_are_keyword_only():
    with pytest.raises(TypeError):
        ContentScorable("hello")  # type: ignore[misc]


class TestMessageScorable:
    def test_carries_the_message_and_filters(self):
        message = _message()
        scorable = MessageScorable(message=message, role_filter="assistant", skip_on_error_result=True)

        assert scorable.message is message
        assert scorable.role_filter == "assistant"
        assert scorable.skip_on_error_result is True

    def test_resolves_to_the_message_it_holds_without_memory(self):
        message = _message()
        memory = _no_memory()

        assert MessageScorable(message=message).resolve_message(memory=memory) is message
        memory.get_message_pieces.assert_not_called()  # type: ignore[attr-defined]


class TestMessageReferenceScorable:
    def test_defaults(self):
        piece_id = uuid.uuid4()
        scorable = MessageReferenceScorable(message_piece_ids=(piece_id,))

        assert scorable.message_piece_ids == (piece_id,)
        assert scorable.role_filter is None
        assert scorable.skip_on_error_result is False

    def test_resolves_from_memory(self, sqlite_instance: MemoryInterface):
        stored = _stored_message()
        sqlite_instance.add_message_to_memory(request=stored)
        scorable = MessageReferenceScorable(message_piece_ids=(stored.get_piece().id,))

        assert scorable.resolve_message(memory=sqlite_instance).get_value() == "stored response"

    def test_raises_when_nothing_is_in_memory(self, sqlite_instance: MemoryInterface):
        missing_id = uuid.uuid4()
        scorable = MessageReferenceScorable(message_piece_ids=(missing_id,))

        with pytest.raises(ValueError, match=f"No message pieces found in memory for ids \\['{missing_id}'\\]"):
            scorable.resolve_message(memory=sqlite_instance)

    def test_raises_naming_only_the_missing_ids(self, sqlite_instance: MemoryInterface):
        """A partial resolution is a caller error, and the error must point at the bad ids."""
        stored = _stored_message()
        sqlite_instance.add_message_to_memory(request=stored)
        stored_id = stored.get_piece().id
        missing_id = uuid.uuid4()
        scorable = MessageReferenceScorable(message_piece_ids=(stored_id, missing_id))

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
        scorable = MessageReferenceScorable(
            message_piece_ids=(first.get_piece().id, second.get_piece().id),
        )

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

    def test_does_not_resolve_a_message(self):
        # It has no message to name, so it must not answer the resolution contract.
        assert not hasattr(ContentScorable(value="hello"), "resolve_message")
