# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Literal, Optional, Union, get_args
from uuid import uuid4

from pyrit.common.deprecation import print_deprecation_message
from pyrit.identifiers import ScorerIdentifier
from pyrit.models.literals import ChatMessageRole, PromptDataType, PromptResponseError
from pyrit.models.score import Score

Originator = Literal["attack", "converter", "undefined", "scorer"]


class MessagePiece:
    """
    Represents a piece of a message to a target.

    This class represents a single piece of a message that will be sent
    to a target. Since some targets can handle multiple pieces (e.g., text and images),
    requests are composed of lists of MessagePiece objects.
    """

    def __init__(
        self,
        *,
        role: ChatMessageRole,
        original_value: str,
        original_value_sha256: Optional[str] = None,
        converted_value: Optional[str] = None,
        converted_value_sha256: Optional[str] = None,
        id: Optional[uuid.UUID | str] = None,
        conversation_id: Optional[str] = None,
        sequence: int = -1,
        labels: Optional[Dict[str, str]] = None,
        prompt_metadata: Optional[Dict[str, Union[str, int]]] = None,
        converter_identifiers: Optional[List[Dict[str, str]]] = None,
        prompt_target_identifier: Optional[Dict[str, str]] = None,
        attack_identifier: Optional[Dict[str, str]] = None,
        scorer_identifier: Optional[Union[ScorerIdentifier, Dict[str, str]]] = None,
        original_value_data_type: PromptDataType = "text",
        converted_value_data_type: Optional[PromptDataType] = None,
        response_error: PromptResponseError = "none",
        originator: Originator = "undefined",
        original_prompt_id: Optional[uuid.UUID] = None,
        timestamp: Optional[datetime] = None,
        scores: Optional[List[Score]] = None,
        targeted_harm_categories: Optional[List[str]] = None,
    ):
        """
        Initialize a MessagePiece.

        Args:
            role: The role of the prompt (system, assistant, user).
            original_value: The text of the original prompt. If prompt is an image, it's a link.
            original_value_sha256: The SHA256 hash of the original prompt data. Defaults to None.
            converted_value: The text of the converted prompt. If prompt is an image, it's a link. Defaults to None.
            converted_value_sha256: The SHA256 hash of the converted prompt data. Defaults to None.
            id: The unique identifier for the memory entry. Defaults to None (auto-generated).
            conversation_id: The identifier for the conversation which is associated with a single target.
                Defaults to None.
            sequence: The order of the conversation within a conversation_id. Defaults to -1.
            labels: The labels associated with the memory entry. Several can be standardized. Defaults to None.
            prompt_metadata: The metadata associated with the prompt. This can be specific to any scenarios.
                Because memory is how components talk with each other, this can be component specific.
                e.g. the URI from a file uploaded to a blob store, or a document type you want to upload.
                Defaults to None.
            converter_identifiers: The converter identifiers for the prompt. Defaults to None.
            prompt_target_identifier: The target identifier for the prompt. Defaults to None.
            attack_identifier: The attack identifier for the prompt. Defaults to None.
            scorer_identifier: The scorer identifier for the prompt. Can be a ScorerIdentifier or a
                dict (deprecated, will be removed in 0.13.0). Defaults to None.
            original_value_data_type: The data type of the original prompt (text, image). Defaults to "text".
            converted_value_data_type: The data type of the converted prompt (text, image). Defaults to "text".
            response_error: The response error type. Defaults to "none".
            originator: The originator of the prompt. Defaults to "undefined".
            original_prompt_id: The original prompt id. It is equal to id unless it is a duplicate. Defaults to None.
            timestamp: The timestamp of the memory entry. Defaults to None (auto-generated).
            scores: The scores associated with the prompt. Defaults to None.
            targeted_harm_categories: The harm categories associated with the prompt. Defaults to None.
        """
        self.id = id if id else uuid4()

        if role not in ChatMessageRole.__args__:  # type: ignore
            raise ValueError(f"Role {role} is not a valid role.")

        self._role: ChatMessageRole = role

        if converted_value is None:
            converted_value = original_value
            if converted_value_data_type is None:
                converted_value_data_type = original_value_data_type
        else:
            # If converted_value is provided but converted_value_data_type is not, default to original_value_data_type
            if converted_value_data_type is None:
                converted_value_data_type = original_value_data_type

        self.conversation_id = conversation_id if conversation_id else str(uuid4())
        self.sequence = sequence

        self.timestamp = timestamp if timestamp else datetime.now()
        self.labels = labels or {}
        self.prompt_metadata = prompt_metadata or {}

        self.converter_identifiers = converter_identifiers if converter_identifiers else []

        self.prompt_target_identifier = prompt_target_identifier or {}
        self.attack_identifier = attack_identifier or {}

        # Handle scorer_identifier: convert dict to ScorerIdentifier with deprecation warning
        if scorer_identifier is None:
            self.scorer_identifier: Optional[ScorerIdentifier] = None
        elif isinstance(scorer_identifier, dict):
            print_deprecation_message(
                old_item="dict for scorer_identifier",
                new_item="ScorerIdentifier",
                removed_in="0.13.0",
            )
            self.scorer_identifier = ScorerIdentifier.from_dict(scorer_identifier)
        else:
            self.scorer_identifier = scorer_identifier

        self.original_value = original_value

        if original_value_data_type not in get_args(PromptDataType):
            raise ValueError(f"original_value_data_type {original_value_data_type} is not a valid data type.")

        self.original_value_data_type: PromptDataType = original_value_data_type

        self.original_value_sha256 = original_value_sha256

        self.converted_value = converted_value

        if converted_value_data_type not in get_args(PromptDataType):
            raise ValueError(f"converted_value_data_type {converted_value_data_type} is not a valid data type.")

        self.converted_value_data_type: PromptDataType = converted_value_data_type

        self.converted_value_sha256 = converted_value_sha256

        if response_error not in get_args(PromptResponseError):
            raise ValueError(f"response_error {response_error} is not a valid response error.")

        self.response_error = response_error
        self.originator = originator

        # Original prompt id defaults to id (assumes that this is the original prompt, not a duplicate)
        self.original_prompt_id = original_prompt_id or self.id

        self.scores = scores if scores else []
        self.targeted_harm_categories = targeted_harm_categories if targeted_harm_categories else []

    async def set_sha256_values_async(self) -> None:
        """
        This method computes the SHA256 hash values asynchronously.
        It should be called after object creation if `original_value` and `converted_value` are set.

        Note, this method is async due to the blob retrieval. And because of that, we opted
        to take it out of main and setter functions. The disadvantage is that it must be explicitly called.
        """
        from pyrit.models.data_type_serializer import data_serializer_factory

        original_serializer = data_serializer_factory(
            category="prompt-memory-entries",
            data_type=self.original_value_data_type,
            value=self.original_value,
        )
        self.original_value_sha256 = await original_serializer.get_sha256()

        converted_serializer = data_serializer_factory(
            category="prompt-memory-entries",
            data_type=self.converted_value_data_type,
            value=self.converted_value,
        )
        self.converted_value_sha256 = await converted_serializer.get_sha256()

    @property
    def api_role(self) -> ChatMessageRole:
        """
        Role to use for API calls.

        Maps simulated_assistant to assistant for API compatibility.
        Use this property when sending messages to external APIs.
        """
        return "assistant" if self._role == "simulated_assistant" else self._role

    @property
    def is_simulated(self) -> bool:
        """
        Check if this is a simulated assistant response.

        Simulated responses come from prepended conversations or generated
        simulated conversations, not from actual target responses.
        """
        return self._role == "simulated_assistant"

    def get_role_for_storage(self) -> ChatMessageRole:
        """
        Get the actual stored role, including simulated_assistant.

        Use this when duplicating messages or preserving role information
        for storage. For API calls or comparisons, use api_role instead.

        Returns:
            The actual role stored (may be simulated_assistant).
        """
        return self._role

    @property
    def role(self) -> ChatMessageRole:
        """
        Deprecated: Use api_role for comparisons or _role for internal storage.

        This property is deprecated and will be removed in a future version.
        Returns api_role for backward compatibility.
        """
        import warnings

        warnings.warn(
            "MessagePiece.role getter is deprecated. Use api_role for comparisons. "
            "This property will be removed in 0.13.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.api_role

    @role.setter
    def role(self, value: ChatMessageRole) -> None:
        """
        Set the role for this message piece.

        Args:
            value: The role to set (system, user, assistant, simulated_assistant, tool, developer).

        Raises:
            ValueError: If the role is not a valid ChatMessageRole.
        """
        if value not in ChatMessageRole.__args__:  # type: ignore
            raise ValueError(f"Role {value} is not a valid role.")
        self._role = value

    def to_message(self) -> Message:  # type: ignore # noqa F821
        from pyrit.models.message import Message

        return Message([self])  # noqa F821

    def has_error(self) -> bool:
        """
        Check if the message piece has an error.
        """
        return self.response_error != "none"

    def is_blocked(self) -> bool:
        """
        Check if the message piece is blocked.
        """
        return self.response_error == "blocked"

    def set_piece_not_in_database(self) -> None:
        """
        Set that the prompt is not in the database.

        This is needed when we're scoring prompts or other things that have not been sent by PyRIT
        """
        self.id = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "role": self._role,
            "conversation_id": self.conversation_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "labels": self.labels,
            "targeted_harm_categories": self.targeted_harm_categories if self.targeted_harm_categories else None,
            "prompt_metadata": self.prompt_metadata,
            "converter_identifiers": self.converter_identifiers,
            "prompt_target_identifier": self.prompt_target_identifier,
            "attack_identifier": self.attack_identifier,
            "scorer_identifier": self.scorer_identifier.to_dict() if self.scorer_identifier else None,
            "original_value_data_type": self.original_value_data_type,
            "original_value": self.original_value,
            "original_value_sha256": self.original_value_sha256,
            "converted_value_data_type": self.converted_value_data_type,
            "converted_value": self.converted_value,
            "converted_value_sha256": self.converted_value_sha256,
            "response_error": self.response_error,
            "originator": self.originator,
            "original_prompt_id": str(self.original_prompt_id),
            "scores": [score.to_dict() for score in self.scores],
        }

    def __str__(self) -> str:
        return f"{self.prompt_target_identifier}: {self._role}: {self.converted_value}"

    __repr__ = __str__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MessagePiece):
            return NotImplemented
        return (
            self.id == other.id
            and self._role == other._role
            and self.original_value == other.original_value
            and self.original_value_data_type == other.original_value_data_type
            and self.original_value_sha256 == other.original_value_sha256
            and self.converted_value == other.converted_value
            and self.converted_value_data_type == other.converted_value_data_type
            and self.converted_value_sha256 == other.converted_value_sha256
            and self.conversation_id == other.conversation_id
            and self.sequence == other.sequence
        )


def sort_message_pieces(message_pieces: list[MessagePiece]) -> list[MessagePiece]:
    """
    Group by conversation_id.
    Order conversations by the earliest timestamp within each conversation_id.
    Within each conversation, order messages by sequence.
    """
    earliest_timestamps = {
        convo_id: min(x.timestamp for x in message_pieces if x.conversation_id == convo_id)
        for convo_id in {x.conversation_id for x in message_pieces}
    }

    # Sort using the precomputed timestamp values, then by sequence
    return sorted(message_pieces, key=lambda x: (earliest_timestamps[x.conversation_id], x.conversation_id, x.sequence))
