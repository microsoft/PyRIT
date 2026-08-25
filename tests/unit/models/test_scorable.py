# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import get_args

import pytest
from pydantic import ValidationError

from pyrit.models import (
    ContentEntryScorable,
    ContentScorable,
    Message,
    MessagePiece,
    MessageScorable,
    Scorable,
    ScorableUnion,
    Score,
    scorable_from_dict,
)


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
    with pytest.raises(ValidationError):
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


@pytest.mark.parametrize(
    "scorable",
    [
        MessageScorable(message_piece_ids=(uuid.uuid4(),)),
        ContentScorable(value="hello", data_type="text"),
        ContentEntryScorable(content_id=uuid.uuid4(), data_type="image_path"),
    ],
)
def test_scorable_round_trips_through_score(scorable: Scorable):
    score = Score(score_value="true", score_type="true_false", scorable=scorable)

    restored = Score.model_validate(score.model_dump(mode="json"))

    assert restored.scorable == scorable
    assert type(restored.scorable) is type(scorable)


def test_stored_scorable_carries_no_type_tag():
    piece_id = uuid.uuid4()

    dumped = MessageScorable(message_piece_ids=(piece_id,)).model_dump(mode="json")

    assert dumped == {"message_piece_ids": [str(piece_id)]}


def test_every_union_member_round_trips_to_its_own_type():
    """Pydantic dispatches on field shape, so members must stay distinguishable.

    A new scorable whose required fields overlap an existing one would silently load as
    the wrong type. Adding a member without a case here fails on the coverage assert.
    """
    cases: list[Scorable] = [
        MessageScorable(message_piece_ids=(uuid.uuid4(),)),
        ContentScorable(value="hello"),
        ContentEntryScorable(content_id=uuid.uuid4()),
    ]

    assert {type(case) for case in cases} == set(get_args(ScorableUnion))

    for case in cases:
        assert type(scorable_from_dict(case.model_dump(mode="json"))) is type(case)


def test_unknown_stored_shape_fails_loudly():
    with pytest.raises(ValidationError):
        scorable_from_dict({"uri": "/data/exfiltrated.txt"})
