# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, model_validator

from pyrit.models.messages.message_piece import MessagePiece

if TYPE_CHECKING:
    from pyrit.models.literals import ChatMessageRole, PromptDataType


class Message(BaseModel):
    """
    Represents a message in a conversation, for example a prompt or a response to a prompt.

    This is a single request to a target. It can contain multiple message pieces.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=False,
    )

    message_pieces: list[MessagePiece]

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def _validate_after(self) -> Message:
        """
        Enforce internal consistency of the message pieces after construction.

        Returns:
            ``self``.
        """
        self._validate_invariants()
        return self

    def _validate_invariants(self) -> None:
        """
        Check that all message pieces are internally consistent.

        Raises:
            ValueError: If the piece collection is empty or contains mismatched conversation IDs,
                sequence numbers, roles, or missing converted values.
        """
        if len(self.message_pieces) == 0:
            raise ValueError("Message must have at least one message piece.")

        conversation_id = self.message_pieces[0].conversation_id
        sequence = self.message_pieces[0].sequence
        role = self.message_pieces[0].role
        for message_piece in self.message_pieces:
            if message_piece.conversation_id != conversation_id:
                raise ValueError("Conversation ID mismatch.")

            if message_piece.sequence != sequence:
                raise ValueError("Inconsistent sequences within the same message entry.")

            if message_piece.converted_value is None:
                raise ValueError("Converted prompt text is None.")

            if message_piece.role != role:
                raise ValueError("Inconsistent roles within the same message entry.")

    def validate(self) -> None:  # type: ignore[ty:invalid-method-override]
        """
        Validate that all message pieces are internally consistent.

        Retained as a public instance method because callers invoke
        ``message.validate()`` directly. Shadows the deprecated
        ``BaseModel.validate`` classmethod.

        Raises:
            ValueError: If piece collection is empty or contains mismatched conversation IDs,
                sequence numbers, roles, or missing converted values.
        """
        self._validate_invariants()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
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
        data_type: PromptDataType | None = None,
        original_value_data_type: PromptDataType | None = None,
        converted_value_data_type: PromptDataType | None = None,
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
        data_type: PromptDataType | None = None,
        original_value_data_type: PromptDataType | None = None,
        converted_value_data_type: PromptDataType | None = None,
    ) -> MessagePiece | None:
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
        The API-compatible role of the first message piece.

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
        The conversation ID of the first request piece.

        Returns:
            str: Conversation identifier.

        Raises:
            ValueError: If the message has no pieces.

        """
        if len(self.message_pieces) == 0:
            raise ValueError("Empty message pieces.")
        return cast("str", self.message_pieces[0].conversation_id)

    @property
    def sequence(self) -> int:
        """
        The sequence value of the first request piece.

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

    def set_response_not_in_memory(self) -> None:
        """
        Mark every piece in this message as ephemeral.

        This is needed when we're scoring prompts or other things that have not been sent by PyRIT.
        Ephemeral pieces are skipped by ``add_message_pieces_to_memory``.
        """
        for piece in self.message_pieces:
            piece.not_in_memory = True

    def set_simulated_role(self) -> None:
        """
        Set the role of all message pieces to simulated_assistant.

        This marks the message as coming from a simulated conversation
        rather than an actual target response.
        """
        for piece in self.message_pieces:
            if piece.role == "assistant":
                piece.role = "simulated_assistant"

    def __str__(self) -> str:
        """
        Return a newline-delimited string representation of message pieces.

        Returns:
            str: Concatenated string representation.

        """
        return "\n".join(f"{piece.role}: {piece.converted_value}" for piece in self.message_pieces)

    @classmethod
    def from_prompt(
        cls,
        *,
        prompt: str,
        role: ChatMessageRole,
        prompt_metadata: dict[str, str | int] | None = None,
    ) -> Message:
        """
        Build a single-piece message from prompt text.

        Args:
            prompt (str): Prompt text.
            role (ChatMessageRole): Role assigned to the message piece.
            prompt_metadata (dict[str, str | int] | None): Optional prompt metadata.

        Returns:
            Message: Constructed message instance.

        """
        piece = MessagePiece(original_value=prompt, role=role, prompt_metadata=prompt_metadata or {})
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

    def duplicate(self) -> Message:
        """
        Create a deep copy of this message with new IDs and timestamp for all message pieces.

        This is useful when you need to reuse a message template but want fresh IDs
        to avoid database conflicts (e.g., during retry attempts).

        The original_prompt_id is intentionally kept the same to track the origin.
        Generates a new timestamp to reflect when the duplicate is created.

        Returns:
            Message: A new Message with deep-copied message pieces, new IDs, and fresh timestamp.

        """
        new_pieces = copy.deepcopy(list(self.message_pieces))
        new_timestamp = datetime.now(tz=timezone.utc)
        for piece in new_pieces:
            piece.id = uuid.uuid4()
            piece.timestamp = new_timestamp
            # original_prompt_id intentionally kept the same to track the origin
        return Message(message_pieces=new_pieces)
