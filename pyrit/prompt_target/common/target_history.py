# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import Message


def filter_non_replayable_messages(*, messages: list[Message]) -> list[Message]:
    """
    Remove failed request/error-response pairs from target-facing history.

    ``processing`` and ``unknown`` responses represent failed exchanges, not
    provider-authored conversation turns. ``blocked`` and ``empty`` responses
    are retained because they are real provider round trips.

    Args:
        messages: Persisted messages that may contain failed exchanges.

    Returns:
        Messages that are safe to include in a later target-facing payload.
    """
    non_replayable_errors = {"processing", "unknown"}
    excluded_indexes: set[int] = set()

    for index, error_response in enumerate(messages):
        if not any(piece.response_error in non_replayable_errors for piece in error_response.message_pieces):
            continue

        excluded_indexes.add(index)
        if index == 0:
            continue

        request = messages[index - 1]
        request_piece = request.get_piece()
        error_piece = error_response.get_piece()
        is_adjacent_request = (
            request.api_role == "user"
            and error_response.api_role == "assistant"
            and bool(request_piece.conversation_id)
            and request_piece.conversation_id == error_piece.conversation_id
            and request_piece.sequence >= 0
            and error_piece.sequence == request_piece.sequence + 1
        )
        if is_adjacent_request:
            excluded_indexes.add(index - 1)

    return [message for index, message in enumerate(messages) if index not in excluded_indexes]
