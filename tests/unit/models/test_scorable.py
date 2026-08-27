# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import uuid

import pytest

from pyrit.models import ContentScorable, Message, MessagePiece, MessageScorable, Scorable


def _message(value: str = "response") -> Message:
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
def test_scorable_is_frozen(scorable: Scorable, field_name: str):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(scorable, field_name, "changed")


@pytest.mark.parametrize(
    "scorable",
    [
        MessageScorable(message_piece_ids=(uuid.uuid4(),)),
        ContentScorable(value="hello"),
    ],
)
def test_every_scorable_is_a_scorable(scorable: Scorable):
    assert isinstance(scorable, Scorable)


def test_scorables_are_inert():
    assert not hasattr(MessageScorable(message_piece_ids=(uuid.uuid4(),)), "resolve_message")
    assert not hasattr(ContentScorable(value="hello"), "to_ephemeral_message")


def test_scorables_are_keyword_only():
    with pytest.raises(TypeError):
        ContentScorable("hello")  # type: ignore[misc]


def test_message_scorable_defaults():
    piece_id = uuid.uuid4()

    scorable = MessageScorable(message_piece_ids=(piece_id,))

    assert scorable.message_piece_ids == (piece_id,)


def test_message_scorable_from_message_names_pieces():
    message = _message()

    scorable = MessageScorable.from_message(message)

    assert scorable.message_piece_ids == (message.get_piece().id,)
    assert not hasattr(scorable, "message")


def test_message_scorable_rejects_empty_ids():
    with pytest.raises(ValueError, match="at least one message piece"):
        MessageScorable(message_piece_ids=())


def test_message_scorable_rejects_duplicate_ids():
    piece_id = uuid.uuid4()

    with pytest.raises(ValueError, match="each message piece once"):
        MessageScorable(message_piece_ids=(piece_id, piece_id))


def test_message_scorable_rejects_ids_that_repeat_across_types():
    piece_id = uuid.uuid4()

    with pytest.raises(ValueError, match="each message piece once"):
        MessageScorable(message_piece_ids=(piece_id, str(piece_id)))


def test_content_scorable_defaults_to_text():
    assert ContentScorable(value="hello").data_type == "text"


def test_content_scorable_from_message_uses_converted_view():
    message = MessagePiece(
        role="user",
        original_value="original",
        converted_value="converted",
        original_value_data_type="text",
        converted_value_data_type="text",
    ).to_message()

    scorable = ContentScorable.from_message(message)

    assert scorable.value == "converted"
    assert scorable.data_type == "text"
