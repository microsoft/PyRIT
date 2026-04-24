# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, Union

from pyrit.common.utils import combine_dict
from pyrit.models.message_piece import MessagePiece

if TYPE_CHECKING:
    from collections.abc import MutableSequence, Sequence

    from pyrit.models.literals import ChatMessageRole, PromptDataType, PromptResponseError


class Message:
    """
    Represents a message in a conversation, for example a prompt or a response to a prompt.

    This is a single request to a target. It can contain multiple message pieces.
    """

    def __init__(self, message_pieces: Sequence[MessagePiece], *, skip_validation: Optional[bool] = False) -> None:
        """
        Initialize a Message from one or more message pieces.

        Args:
            message_pieces (Sequence[MessagePiece]): Pieces belonging to the same message turn.
            skip_validation (Optional[bool]): Whether to skip consistency validation.

        Raises:
            ValueError: If no message pieces are provided.

        """
        if not message_pieces:
            raise ValueError("Message must have at least one message piece.")
        self.message_pieces = message_pieces
        if not skip_validation:
            self.validate()

    def get_value(self, n: int = 0) -> str:
        """
        Return the converted value of the nth message piece.

        Args:
            n (int): Zero-based index of the piece to read.

        Returns:
            str: Converted value of the selected message piece.

        Raises:
            IndexError: If the index is out of bounds.

        """
        if n >= len(self.message_pieces):
            raise IndexError(f"No message piece at index {n}.")
        return self.message_pieces[n].converted_value

    def get_values(self) -> list[str]:
        """
        Return the converted values of all message pieces.

        Returns:
            list[str]: Converted values for all message pieces.

        """
        return [message_piece.converted_value for message_piece in self.message_pieces]

    def get_piece(self, n: int = 0) -> MessagePiece:
        """
        Return the nth message piece.

        Args:
            n (int): Zero-based index of the piece to return.

        Returns:
            MessagePiece: Selected message piece.

        Raises:
            ValueError: If the message has no pieces.
            IndexError: If the index is out of bounds.

        """
        if len(self.message_pieces) == 0:
            raise ValueError("Empty message pieces.")

        if n >= len(self.message_pieces):
            raise IndexError(f"No message piece at index {n}.")

        return self.message_pieces[n]

    def get_pieces_by_type(
        self,
        *,
        data_type: Optional[PromptDataType] = None,
        original_value_data_type: Optional[PromptDataType] = None,
        converted_value_data_type: Optional[PromptDataType] = None,
    ) -> list[MessagePiece]:
        """
        Return all message pieces matching the given data type.

        Args:
            data_type: Alias for converted_value_data_type (for convenience).
            original_value_data_type: The original_value_data_type to filter by.
            converted_value_data_type: The converted_value_data_type to filter by.

        Returns:
            A list of matching MessagePiece objects (may be empty).

        """
        effective_converted = converted_value_data_type or data_type
        results = self.message_pieces
        if effective_converted:
            results = [p for p in results if p.converted_value_data_type == effective_converted]
        if original_value_data_type:
            results = [p for p in results if p.original_value_data_type == original_value_data_type]
        return list(results)

    def get_piece_by_type(
        self,
        *,
        data_type: Optional[PromptDataType] = None,
        original_value_data_type: Optional[PromptDataType] = None,
        converted_value_data_type: Optional[PromptDataType] = None,
    ) -> Optional[MessagePiece]:
        """
        Return the first message piece matching the given data type, or None.

        Args:
            data_type: Alias for converted_value_data_type (for convenience).
            original_value_data_type: The original_value_data_type to filter by.
            converted_value_data_type: The converted_value_data_type to filter by.

        Returns:
            The first matching MessagePiece, or None if no match is found.

        """
        pieces = self.get_pieces_by_type(
            data_type=data_type,
            original_value_data_type=original_value_data_type,
            converted_value_data_type=converted_value_data_type,
        )
        return pieces[0] if pieces else None

    @property
    def api_role(self) -> ChatMessageRole:
        """
        Return the API-compatible role of the first message piece.

        Maps simulated_assistant to assistant for API compatibility.
        All message pieces in a Message should have the same role.

        Returns:
            ChatMessageRole: Role compatible with external API calls.

        Raises:
            ValueError: If the message has no pieces.

        """
        if len(self.message_pieces) == 0:
            raise ValueError("Empty message pieces.")
        return self.message_pieces[0].api_role

    @property
    def is_simulated(self) -> bool:
        """
        Check if this is a simulated assistant response.

        Simulated responses come from prepended conversations or generated
        simulated conversations, not from actual target responses.
        """
        if len(self.message_pieces) == 0:
            return False
        return self.message_pieces[0].is_simulated

    @property
    def conversation_id(self) -> str:
        """
        Return the conversation ID of the first request piece.

        Returns:
            str: Conversation identifier.

        Raises:
            ValueError: If the message has no pieces.

        """
        if len(self.message_pieces) == 0:
            raise ValueError("Empty message pieces.")
        return self.message_pieces[0].conversation_id

    @property
    def sequence(self) -> int:
        """
        Return the sequence value of the first request piece.

        Returns:
            int: Sequence number for the message turn.

        Raises:
            ValueError: If the message has no pieces.

        """
        if len(self.message_pieces) == 0:
            raise ValueError("Empty message pieces.")
        return self.message_pieces[0].sequence

    def is_error(self) -> bool:
        """
        Check whether any message piece indicates an error.

        Returns:
            bool: True when any piece has a non-none error flag or error data type.

        """
        for piece in self.message_pieces:
            if piece.response_error != "none" or piece.converted_value_data_type == "error":
                return True
        return False

    def set_response_not_in_database(self) -> None:
        """
        Set that the prompt is not in the database.

        This is needed when we're scoring prompts or other things that have not been sent by PyRIT
        """
        for piece in self.message_pieces:
            piece.set_piece_not_in_database()

    def set_simulated_role(self) -> None:
        """
        Set the role of all message pieces to simulated_assistant.

        This marks the message as coming from a simulated conversation
        rather than an actual target response.
        """
        for piece in self.message_pieces:
            if piece._role == "assistant":
                piece._role = "simulated_assistant"

    def validate(self) -> None:
        """
        Validate that all message pieces are internally consistent.

        Raises:
            ValueError: If piece collection is empty or contains mismatched conversation IDs,
                sequence numbers, roles, or missing converted values.

        """
        if len(self.message_pieces) == 0:
            raise ValueError("Empty message pieces.")

        conversation_id = self.message_pieces[0].conversation_id
        sequence = self.message_pieces[0].sequence
        role = self.message_pieces[0]._role
        for message_piece in self.message_pieces:
            if message_piece.conversation_id != conversation_id:
                raise ValueError("Conversation ID mismatch.")

            if message_piece.sequence != sequence:
                raise ValueError("Inconsistent sequences within the same message entry.")

            if message_piece.converted_value is None:
                raise ValueError("Converted prompt text is None.")

            if message_piece._role != role:
                raise ValueError("Inconsistent roles within the same message entry.")

    def __str__(self) -> str:
        """
        Return a newline-delimited string representation of message pieces.

        Returns:
            str: Concatenated string representation.

        """
        ret = ""
        for message_piece in self.message_pieces:
            ret += str(message_piece) + "\n"
        return "\n".join([str(message_piece) for message_piece in self.message_pieces])

    def to_dict(self) -> dict[str, object]:
        """
        Convert the message to a dictionary representation.

        Returns:
            dict: A dictionary with 'role', 'converted_value', 'conversation_id', 'sequence',
                and 'converted_value_data_type' keys.

        """
        if len(self.message_pieces) == 1:
            converted_value: str | list[str] = self.message_pieces[0].converted_value
            converted_value_data_type: str | list[str] = self.message_pieces[0].converted_value_data_type
        else:
            converted_value = [piece.converted_value for piece in self.message_pieces]
            converted_value_data_type = [piece.converted_value_data_type for piece in self.message_pieces]

        return {
            "role": self.api_role,
            "converted_value": converted_value,
            "conversation_id": self.conversation_id,
            "sequence": self.sequence,
            "converted_value_data_type": converted_value_data_type,
        }

    @staticmethod
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

    @staticmethod
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

    @classmethod
    def from_prompt(
        cls,
        *,
        prompt: str,
        role: ChatMessageRole,
        prompt_metadata: Optional[dict[str, Union[str, int]]] = None,
    ) -> Message:
        """
        Build a single-piece message from prompt text.

        Args:
            prompt (str): Prompt text.
            role (ChatMessageRole): Role assigned to the message piece.
            prompt_metadata (Optional[Dict[str, Union[str, int]]]): Optional prompt metadata.

        Returns:
            Message: Constructed message instance.

        """
        piece = MessagePiece(original_value=prompt, role=role, prompt_metadata=prompt_metadata)
        return cls(message_pieces=[piece])

    @classmethod
    def from_system_prompt(cls, system_prompt: str) -> Message:
        """
        Build a message from a system prompt.

        Args:
            system_prompt (str): System instruction text.

        Returns:
            Message: Constructed system-role message.

        """
        return cls.from_prompt(prompt=system_prompt, role="system")

    def duplicate_message(self) -> Message:
        """
        Create a deep copy of this message with new IDs and timestamp for all message pieces.

        This is useful when you need to reuse a message template but want fresh IDs
        to avoid database conflicts (e.g., during retry attempts).

        The original_prompt_id is intentionally kept the same to track the origin.
        Generates a new timestamp to reflect when the duplicate is created.

        Returns:
            Message: A new Message with deep-copied message pieces, new IDs, and fresh timestamp.

        """
        new_pieces = copy.deepcopy(self.message_pieces)
        new_timestamp = datetime.now(tz=timezone.utc)
        for piece in new_pieces:
            piece.id = uuid.uuid4()
            piece.timestamp = new_timestamp
            # original_prompt_id intentionally kept the same to track the origin
        return Message(message_pieces=new_pieces)


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
    return [Message(conversation_by_sequence[seq]) for seq in sorted_sequences]


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
    conversations: dict[str, list[MessagePiece]] = {}
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
    prompt_metadata: Optional[dict[str, Union[str, int]]] = None,
    error: PromptResponseError = "none",
) -> Message:
    """
    Construct a response message from a request message piece.

    Args:
        request (MessagePiece): Source request message piece.
        response_text_pieces (list[str]): Response values to include.
        response_type (PromptDataType): Data type for original and converted response values.
        prompt_metadata (Optional[Dict[str, Union[str, int]]]): Additional metadata to merge.
        error (PromptResponseError): Error classification for the response.

    Returns:
        Message: Constructed response message.

    """
    if request.prompt_metadata:
        prompt_metadata = combine_dict(request.prompt_metadata, prompt_metadata or {})

    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=resp_text,
                conversation_id=request.conversation_id,
                labels=request.labels,
                prompt_target_identifier=request.prompt_target_identifier,
                attack_identifier=request.attack_identifier,
                original_value_data_type=response_type,
                converted_value_data_type=response_type,
                prompt_metadata=prompt_metadata,
                response_error=error,
            )
            for resp_text in response_text_pieces
        ]
    )
