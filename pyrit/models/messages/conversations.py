# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""``Conversation`` model plus helpers that operate on collections of ``Message`` / ``MessagePiece``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.messages.conversation_retry import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    ConversationRetry,
)
from pyrit.models.messages.message import Message
from pyrit.models.messages.message_piece import MessagePiece
from pyrit.models.score import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    ComponentIdentifierField,
)

if TYPE_CHECKING:
    from collections.abc import MutableSequence, Sequence

    from pyrit.models.literals import PromptDataType, PromptResponseError


class Conversation(BaseModel):
    """
    Conversation-scoped metadata shared by every piece in a conversation.

    A ``Conversation`` records state that belongs to the conversation as a whole
    rather than to any individual ``MessagePiece`` -- most importantly the target
    the conversation is held with, plus the record of any turns that were retried.
    Persisting the per-conversation identifiers once here (instead of stamping them
    onto every piece/row) is what keeps ``MessagePiece`` small.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=False,
    )

    conversation_id: str
    target_identifier: ComponentIdentifierField | None = None

    # Turns that were retried (rolled back out of memory and resent) in this conversation.
    retries: list[ConversationRetry] = Field(default_factory=list)


def get_all_values(messages: Sequence[Message]) -> list[str]:
    """
    Return all converted values across the provided messages.

    Args:
        messages (Sequence[Message]): Messages to aggregate.

    Returns:
        list[str]: Flattened list of converted values.

    """
    values: list[str] = []
    for message in messages:
        values.extend(message.get_values())
    return values


def flatten_to_message_pieces(
    messages: Sequence[Message],
) -> MutableSequence[MessagePiece]:
    """
    Flatten messages into a single list of message pieces.

    Args:
        messages (Sequence[Message]): Messages to flatten.

    Returns:
        MutableSequence[MessagePiece]: Flattened message pieces.

    """
    if not messages:
        return []
    message_pieces: MutableSequence[MessagePiece] = []

    for response in messages:
        message_pieces.extend(response.message_pieces)

    return message_pieces


def group_conversation_message_pieces_by_sequence(
    message_pieces: Sequence[MessagePiece],
) -> MutableSequence[Message]:
    """
    Group message pieces from the same conversation into messages.

    This is done using the sequence number and conversation ID.

    Args:
        message_pieces (Sequence[MessagePiece]): A list of MessagePiece objects representing individual
            message pieces.

    Returns:
        MutableSequence[Message]: A list of Message objects representing grouped message
            pieces. This is ordered by the sequence number.

    Raises:
        ValueError: If the conversation ID of any message piece does not match the conversation ID of the first
            message piece.

    Example:
    >>> message_pieces = [
    >>>     MessagePiece(conversation_id=1, sequence=1, text="Given this list of creatures, which is your
    >>>     favorite:"),
    >>>     MessagePiece(conversation_id=1, sequence=2, text="Good question!"),
    >>>     MessagePiece(conversation_id=1, sequence=1, text="Raccoon, Narwhal, or Sloth?"),
    >>>     MessagePiece(conversation_id=1, sequence=2, text="I'd have to say raccoons are my favorite!"),
    >>> ]
    >>> grouped_responses = group_conversation_message_pieces(message_pieces)
    ... [
    ...     Message(message_pieces=[
    ...         MessagePiece(conversation_id=1, sequence=1, text="Given this list of creatures, which is your
    ...         favorite:"),
    ...         MessagePiece(conversation_id=1, sequence=1, text="Raccoon, Narwhal, or Sloth?")
    ...     ]),
    ...     Message(message_pieces=[
    ...         MessagePiece(conversation_id=1, sequence=2, text="Good question!"),
    ...         MessagePiece(conversation_id=1, sequence=2, text="I'd have to say raccoons are my favorite!")
    ...     ])
    ... ]

    """
    if not message_pieces:
        return []

    conversation_id = message_pieces[0].conversation_id

    conversation_by_sequence: dict[int, list[MessagePiece]] = {}

    for message_piece in message_pieces:
        if message_piece.conversation_id != conversation_id:
            raise ValueError(
                f"All message pieces must be from the same conversation. "
                f"Expected conversation_id='{conversation_id}', but found '{message_piece.conversation_id}'. "
                f"If grouping pieces from multiple conversations, group by conversation_id first."
            )

        if message_piece.sequence not in conversation_by_sequence:
            conversation_by_sequence[message_piece.sequence] = []
        conversation_by_sequence[message_piece.sequence].append(message_piece)

    sorted_sequences = sorted(conversation_by_sequence.keys())
    return [Message(message_pieces=conversation_by_sequence[seq]) for seq in sorted_sequences]


def group_message_pieces_into_conversations(
    message_pieces: Sequence[MessagePiece],
) -> list[list[Message]]:
    """
    Group message pieces from multiple conversations into separate conversation groups.

    This function first groups pieces by conversation ID, then groups each conversation's
    pieces by sequence number. Each conversation is returned as a separate list of
    Message objects.

    Args:
        message_pieces (Sequence[MessagePiece]): A list of MessagePiece objects from
            potentially different conversations.

    Returns:
        list[list[Message]]: A list of conversations, where each conversation is a list
            of Message objects grouped by sequence.

    Example:
    >>> message_pieces = [
    >>>     MessagePiece(conversation_id="conv1", sequence=1, text="Hello"),
    >>>     MessagePiece(conversation_id="conv2", sequence=1, text="Hi there"),
    >>>     MessagePiece(conversation_id="conv1", sequence=2, text="How are you?"),
    >>>     MessagePiece(conversation_id="conv2", sequence=2, text="I'm good"),
    >>> ]
    >>> conversations = group_message_pieces_into_conversations(message_pieces)
    >>> # Returns a list of 2 conversations:
    >>> # [
    >>> #   [Message(seq=1), Message(seq=2)],  # conv1
    >>> #   [Message(seq=1), Message(seq=2)]   # conv2
    >>> # ]

    """
    if not message_pieces:
        return []

    # Group pieces by conversation ID
    conversations: dict[str | None, list[MessagePiece]] = {}
    for piece in message_pieces:
        conv_id = piece.conversation_id
        if conv_id not in conversations:
            conversations[conv_id] = []
        conversations[conv_id].append(piece)

    # For each conversation, group by sequence
    result: list[list[Message]] = []
    for conv_pieces in conversations.values():
        responses = group_conversation_message_pieces_by_sequence(conv_pieces)
        result.append(list(responses))

    return result


def construct_response_from_request(
    request: MessagePiece,
    response_text_pieces: list[str],
    response_type: PromptDataType = "text",
    prompt_metadata: dict[str, str | int] | None = None,
    error: PromptResponseError = "none",
) -> Message:
    """
    Construct a response message from a request message piece.

    Args:
        request (MessagePiece): Source request message piece.
        response_text_pieces (list[str]): Response values to include.
        response_type (PromptDataType): Data type for original and converted response values.
        prompt_metadata (dict[str, str | int] | None): Additional metadata to merge.
        error (PromptResponseError): Error classification for the response.

    Returns:
        Message: Constructed response message.

    """
    if request.prompt_metadata:
        prompt_metadata = {**request.prompt_metadata, **(prompt_metadata or {})}

    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=resp_text,
                conversation_id=request.conversation_id,
                labels=request.labels,
                original_value_data_type=response_type,
                converted_value_data_type=response_type,
                prompt_metadata=prompt_metadata or {},
                response_error=error,
            )
            for resp_text in response_text_pieces
        ]
    )
