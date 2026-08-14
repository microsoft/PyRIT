# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import uuid

import pytest

from pyrit.models import (
    ContentScorable,
    ConversationScorable,
    MessagePiece,
    MessageReferenceScorable,
    MessageScorable,
    OutputMatches,
    ScoringExpectation,
    ScoringScope,
    SurfaceScorable,
    ToolCalled,
    ToolSequence,
    TraceScorable,
    Volatility,
)


def _message():
    return MessagePiece(role="assistant", original_value="hello").to_message()


@pytest.mark.parametrize(
    "scorable, expected_volatility",
    [
        (ConversationScorable(conversation_id="c1"), Volatility.APPEND_ONLY),
        (MessageScorable(message=_message()), Volatility.STABLE),
        (MessageReferenceScorable(message_piece_ids=(uuid.uuid4(),)), Volatility.STABLE),
        (ContentScorable(value="hello"), Volatility.STABLE),
        (SurfaceScorable(uri="/tmp/out.txt"), Volatility.MUTABLE),
        (TraceScorable(trace_ids=("t1",)), Volatility.APPEND_ONLY),
    ],
)
def test_scorable_volatility(scorable, expected_volatility):
    assert scorable.volatility is expected_volatility


@pytest.mark.parametrize(
    "scorable",
    [
        ConversationScorable(conversation_id="c1"),
        MessageReferenceScorable(message_piece_ids=(uuid.uuid4(),)),
        ContentScorable(value="hello"),
        SurfaceScorable(uri="/tmp/out.txt"),
        TraceScorable(trace_ids=("t1",)),
    ],
)
def test_scorable_is_frozen(scorable):
    with pytest.raises(dataclasses.FrozenInstanceError):
        scorable.volatility = Volatility.MUTABLE


def test_conversation_scorable_defaults():
    scorable = ConversationScorable(conversation_id="c1")

    assert scorable.through_sequence is None
    assert scorable.role_filter is None


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


def test_surface_scorable_defaults_to_file():
    scorable = SurfaceScorable(uri="/tmp/out.txt")

    assert scorable.surface == "file"
    assert scorable.scope is None


def test_trace_scorable_accepts_a_scope():
    scope = ScoringScope(window="last_turn")
    scorable = TraceScorable(trace_ids=("t1",), span_name="tool_call", scope=scope)

    assert scorable.scope is scope
    assert scorable.span_name == "tool_call"


def test_scoring_scope_defaults():
    scope = ScoringScope()

    assert scope.window is None
    assert scope.labels == {}


def test_expectation_defaults():
    expectation = ScoringExpectation()

    assert expectation.objective is None
    assert expectation.conditions == ()
    assert expectation.extra == {}


def test_expectation_carries_conditions():
    conditions = (ToolCalled(name="send_email"), ToolSequence(names=("a", "b")), OutputMatches(value="42"))
    expectation = ScoringExpectation(objective="exfiltrate", conditions=conditions)

    assert expectation.objective == "exfiltrate"
    assert expectation.conditions == conditions


def test_expectation_is_frozen():
    expectation = ScoringExpectation(objective="exfiltrate")

    with pytest.raises(dataclasses.FrozenInstanceError):
        expectation.objective = "something else"


def test_tool_called_defaults():
    condition = ToolCalled(name="send_email")

    assert condition.name == "send_email"
    assert condition.arguments is None


def test_expectations_with_equal_values_compare_equal():
    assert ScoringExpectation(objective="a", conditions=(ToolCalled(name="t"),)) == ScoringExpectation(
        objective="a", conditions=(ToolCalled(name="t"),)
    )
