# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models import ChatMessageRole, Message, PromptResponseError
from pyrit.prompt_target.common.target_history import filter_non_replayable_messages


def _message(
    *,
    role: ChatMessageRole,
    value: str,
    sequence: int,
    conversation_id: str = "conversation",
) -> Message:
    message = Message.from_prompt(prompt=value, role=role)
    piece = message.get_piece()
    piece.sequence = sequence
    piece.conversation_id = conversation_id
    return message


@pytest.mark.parametrize("response_error", ["processing", "unknown"])
def test_filter_removes_adjacent_failed_exchange(response_error: PromptResponseError) -> None:
    successful = _message(role="user", value="successful", sequence=0)
    failed_request = _message(role="user", value="failed request", sequence=1)
    error_response = _message(role="assistant", value="private stack trace", sequence=2)
    error_response.get_piece().response_error = response_error

    filtered = filter_non_replayable_messages(messages=[successful, failed_request, error_response])

    assert filtered == [successful]


@pytest.mark.parametrize(
    ("preceding_role", "preceding_conversation_id", "preceding_sequence"),
    [
        ("assistant", "conversation", 4),
        ("user", "other-conversation", 4),
        ("user", "conversation", 3),
    ],
)
def test_filter_does_not_remove_unrelated_preceding_message(
    preceding_role: ChatMessageRole,
    preceding_conversation_id: str,
    preceding_sequence: int,
) -> None:
    preceding = _message(
        role=preceding_role,
        value="unrelated",
        sequence=preceding_sequence,
        conversation_id=preceding_conversation_id,
    )
    error_response = _message(role="assistant", value="private stack trace", sequence=5)
    error_response.get_piece().response_error = "processing"

    filtered = filter_non_replayable_messages(messages=[preceding, error_response])

    assert filtered == [preceding]


@pytest.mark.parametrize("response_error", ["blocked", "empty"])
def test_filter_retains_provider_round_trip(response_error: PromptResponseError) -> None:
    request = _message(role="user", value="request", sequence=0)
    response = _message(role="assistant", value="provider response", sequence=1)
    response.get_piece().response_error = response_error

    filtered = filter_non_replayable_messages(messages=[request, response])

    assert filtered == [request, response]
