# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import uuid

import pytest

from pyrit.models import ContentScorable, MessagePiece, MessageReferenceScorable, MessageScorable, ScoringExpectation


def _message():
    return MessagePiece(role="assistant", original_value="hello").to_message()


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


def test_message_scorable_carries_the_message():
    message = _message()
    scorable = MessageScorable(message=message, role_filter="assistant", skip_on_error_result=True)

    assert scorable.message is message
    assert scorable.role_filter == "assistant"
    assert scorable.skip_on_error_result is True


def test_message_reference_scorable_defaults():
    piece_id = uuid.uuid4()
    scorable = MessageReferenceScorable(message_piece_ids=(piece_id,))

    assert scorable.message_piece_ids == (piece_id,)
    assert scorable.role_filter is None
    assert scorable.skip_on_error_result is False


def test_content_scorable_defaults_to_text():
    assert ContentScorable(value="hello").data_type == "text"


def test_expectation_defaults():
    assert ScoringExpectation().objective is None


def test_expectation_is_frozen():
    expectation = ScoringExpectation(objective="exfiltrate")

    with pytest.raises(dataclasses.FrozenInstanceError):
        expectation.objective = "something else"


def test_expectations_with_equal_values_compare_equal():
    assert ScoringExpectation(objective="a") == ScoringExpectation(objective="a")
