# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional, Sequence, get_args

from pyrit.models import ChatMessageRole, Message, MessagePiece, PromptDataType


class ScorerPromptValidator:
    """
    Validates message pieces and scorer configurations.

    This class provides validation for scorer inputs, ensuring that message pieces meet
    required criteria such as data types, roles, and metadata requirements.
    """

    def __init__(
        self,
        *,
        supported_data_types: Optional[Sequence[PromptDataType]] = None,
        required_metadata: Optional[Sequence[str]] = None,
        supported_roles: Optional[Sequence[ChatMessageRole]] = None,
        max_pieces_in_response: Optional[int] = None,
        max_text_length: Optional[int] = None,
        enforce_all_pieces_valid: Optional[bool] = False,
        raise_on_no_valid_pieces: Optional[bool] = False,
        is_objective_required: bool = False,
    ) -> None:
        """
        Initialize the ScorerPromptValidator.

        Args:
            supported_data_types (Optional[Sequence[PromptDataType]]): Data types that the scorer supports.
                Defaults to all data types if not provided.
            required_metadata (Optional[Sequence[str]]): Metadata keys that must be present in message pieces.
                Defaults to empty list.
            supported_roles (Optional[Sequence[ChatMessageRole]]): Message roles that the scorer supports.
                Defaults to all roles if not provided.
            max_pieces_in_response (Optional[int]): Maximum number of pieces allowed in a response.
                Defaults to None (no limit).
            max_text_length (Optional[int]): Maximum character length for text data type pieces.
                Defaults to None (no limit).
            enforce_all_pieces_valid (Optional[bool]): Whether all pieces must be valid or just at least one.
                Defaults to False.
            raise_on_no_valid_pieces (Optional[bool]): Whether to raise ValueError when no pieces are valid.
                Defaults to False, allowing scorers to handle empty results gracefully (e.g., returning
                False for blocked responses). Set to True to raise an exception instead.
            is_objective_required (bool): Whether an objective must be provided for scoring. Defaults to False.
        """
        if supported_data_types:
            self._supported_data_types = supported_data_types
        else:
            self._supported_data_types = get_args(PromptDataType)

        if supported_roles:
            self._supported_roles = supported_roles
        else:
            self._supported_roles = get_args(ChatMessageRole)

        self._required_metadata = required_metadata or []

        self._max_pieces_in_response = max_pieces_in_response
        self._max_text_length = max_text_length
        self._enforce_all_pieces_valid = enforce_all_pieces_valid
        self._raise_on_no_valid_pieces = raise_on_no_valid_pieces

        self._is_objective_required = is_objective_required

    def validate(self, message: Message, objective: str | None) -> None:
        """
        Validate a message and objective against configured requirements.

        Args:
            message (Message): The message to validate.
            objective (str | None): The objective string, if required.

        Raises:
            ValueError: If validation fails due to unsupported pieces, exceeding max pieces, or missing objective.
        """
        valid_pieces_count = 0
        for piece in message.message_pieces:
            if self.is_message_piece_supported(piece):
                valid_pieces_count += 1
            elif self._enforce_all_pieces_valid:
                raise ValueError(
                    f"Message piece {piece.id} with data type {piece.converted_value_data_type} is not supported."
                )

        if valid_pieces_count < 1 and self._raise_on_no_valid_pieces:
            attempted_metadata = [getattr(piece, "prompt_metadata", None) for piece in message.message_pieces]
            raise ValueError(
                "There are no valid pieces to score. \n\n"
                f"Required types: {self._supported_data_types}. "
                f"Required metadata: {self._required_metadata}. "
                f"Length limit: {self._max_pieces_in_response}. "
                f"Objective required: {self._is_objective_required}. "
                f"Message pieces: {message.message_pieces}. "
                f"Prompt metadata: {attempted_metadata}. "
                f"Objective included: {objective}. "
            )

        if self._max_pieces_in_response is not None:
            if len(message.message_pieces) > self._max_pieces_in_response:
                raise ValueError(
                    f"Message has {len(message.message_pieces)} pieces, "
                    f"exceeding the limit of {self._max_pieces_in_response}."
                )

        if self._is_objective_required and not objective:
            raise ValueError("Objective is required but not provided.")

    def is_message_piece_supported(self, message_piece: MessagePiece) -> bool:
        """
        Check if a message piece is supported by this validator.

        Args:
            message_piece (MessagePiece): The message piece to check.

        Returns:
            bool: True if the message piece meets all validation criteria, False otherwise.
        """
        if message_piece.converted_value_data_type not in self._supported_data_types:
            return False

        for metadata in self._required_metadata:
            if metadata not in message_piece.prompt_metadata:
                return False

        if message_piece.api_role not in self._supported_roles:
            return False

        # Check text length limit for text data types
        if self._max_text_length is not None and message_piece.converted_value_data_type == "text":
            text_length = len(message_piece.converted_value) if message_piece.converted_value else 0
            if text_length > self._max_text_length:
                return False

        return True
