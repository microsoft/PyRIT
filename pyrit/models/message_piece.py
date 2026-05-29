# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Optional, Union, get_args
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from pyrit.common.deprecation import print_deprecation_message
from pyrit.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.data_type_serializer import data_serializer_factory
from pyrit.models.literals import ChatMessageRole, PromptDataType, PromptResponseError
from pyrit.models.score import Score

if TYPE_CHECKING:
    from pyrit.models.message import Message

    Originator = Literal["attack", "converter", "undefined", "scorer"]


_OriginatorLiteral = Literal["attack", "converter", "undefined", "scorer"]


def __getattr__(name: str) -> Any:
    """
    Lazily resolve deprecated module-level aliases.

    Returns:
        Any: The resolved deprecated alias.

    Raises:
        AttributeError: If the attribute name is not recognized.
    """
    if name == "Originator":
        print_deprecation_message(
            old_item="pyrit.models.message_piece.Originator",
            new_item=(
                "inline Literal['attack', 'converter', 'undefined', 'scorer'] "
                "(the type alias is being removed; the originator field itself is "
                "deprecated and will be removed in 0.16.0)"
            ),
            removed_in="0.16.0",
        )
        return Literal["attack", "converter", "undefined", "scorer"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _suppress_deprecation(info: ValidationInfo) -> bool:
    context = info.context or {}
    return bool(context.get("suppress_deprecation_warnings"))


class MessagePiece(BaseModel):
    """
    Represents a piece of a message to a target.

    This class represents a single piece of a message that will be sent
    to a target. Since some targets can handle multiple pieces (e.g., text and images),
    requests are composed of lists of MessagePiece objects.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=False,
        populate_by_name=True,
    )

    # Fields declared in the order produced by ``to_dict`` for serialization parity.
    id: Optional[Union[uuid.UUID, str]] = Field(default_factory=uuid4)  # noqa: A003
    role: ChatMessageRole
    conversation_id: str = Field(default_factory=lambda: str(uuid4()))
    sequence: int = -1
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    labels: dict[str, Any] = Field(default_factory=dict)
    targeted_harm_categories: list[str] = Field(default_factory=list)
    prompt_metadata: dict[str, Any] = Field(default_factory=dict)
    converter_identifiers: list[ComponentIdentifier] = Field(default_factory=list)
    prompt_target_identifier: Optional[ComponentIdentifier] = None
    attack_identifier: Optional[ComponentIdentifier] = None
    scorer_identifier: Optional[ComponentIdentifier] = None
    original_value_data_type: PromptDataType = "text"
    original_value: str
    original_value_sha256: Optional[str] = None
    converted_value_data_type: PromptDataType = "text"
    converted_value: str = ""
    converted_value_sha256: Optional[str] = None
    response_error: PromptResponseError = "none"
    originator: _OriginatorLiteral = "undefined"
    original_prompt_id: Optional[uuid.UUID] = None
    scores: list[Score] = Field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Pre-validation: drop explicit ``None`` overrides, emit deprecation
    # warnings, and apply the legacy default-derivation rules.
    # ------------------------------------------------------------------ #
    @model_validator(mode="before")
    @classmethod
    def _normalize_inputs(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return data

        # Allow ``None`` to mean "use the default" for fields with default_factory or default.
        for key in (
            "id",
            "conversation_id",
            "timestamp",
            "labels",
            "targeted_harm_categories",
            "prompt_metadata",
            "converter_identifiers",
            "scores",
            "original_prompt_id",
        ):
            if key in data and data[key] is None:
                del data[key]

        # Deprecation warnings (suppressed when called via ``from_dict``).
        if not _suppress_deprecation(info):
            if "labels" in data and data["labels"] is not None:
                print_deprecation_message(
                    old_item="MessagePiece(..., labels=...)",
                    new_item="MessagePiece(...)",
                    removed_in="0.17.0",
                )
            if "scorer_identifier" in data and data["scorer_identifier"] is not None:
                print_deprecation_message(
                    old_item="MessagePiece(..., scorer_identifier=...)",
                    new_item="MessagePiece(...)",
                    removed_in="0.16.0",
                )
            if "originator" in data and data["originator"] != "undefined":
                print_deprecation_message(
                    old_item="MessagePiece(..., originator=...)",
                    new_item="MessagePiece(...)",
                    removed_in="0.16.0",
                )
            if "scores" in data and data["scores"] is not None:
                print_deprecation_message(
                    old_item="MessagePiece(..., scores=...)",
                    new_item="MessagePiece(...)",
                    removed_in="0.16.0",
                )
            if "targeted_harm_categories" in data and data["targeted_harm_categories"] is not None:
                print_deprecation_message(
                    old_item="MessagePiece(..., targeted_harm_categories=...)",
                    new_item="MessagePiece(...)",
                    removed_in="0.16.0",
                )

        # Mirror the legacy default-derivation for ``converted_value`` and types.
        original_value = data.get("original_value")
        original_dtype = data.get("original_value_data_type")
        converted_value = data.get("converted_value")
        converted_dtype = data.get("converted_value_data_type")

        if converted_value is None and original_value is not None:
            data["converted_value"] = original_value
            if converted_dtype is None:
                data["converted_value_data_type"] = original_dtype if original_dtype is not None else "text"
        elif converted_dtype is None:
            data["converted_value_data_type"] = original_dtype if original_dtype is not None else "text"

        return data

    @model_validator(mode="after")
    def _default_original_prompt_id(self) -> MessagePiece:
        if self.original_prompt_id is None and isinstance(self.id, uuid.UUID):
            object.__setattr__(self, "original_prompt_id", self.id)
        return self

    # ------------------------------------------------------------------ #
    # Field-level validators that preserve legacy error messages and
    # support deserialization from dicts (ComponentIdentifier / Score).
    # ------------------------------------------------------------------ #
    @field_validator("role", mode="before")
    @classmethod
    def _validate_role(cls, value: Any) -> Any:
        if value not in get_args(ChatMessageRole):
            raise ValueError(f"Role {value} is not a valid role.")
        return value

    @field_validator("original_value_data_type", "converted_value_data_type", mode="before")
    @classmethod
    def _validate_data_type(cls, value: Any) -> Any:
        if value is None:
            return value
        if value not in get_args(PromptDataType):
            raise ValueError(f"{value} is not a valid data type.")
        return value

    @field_validator("response_error", mode="before")
    @classmethod
    def _validate_response_error(cls, value: Any) -> Any:
        if value not in get_args(PromptResponseError):
            raise ValueError(f"response_error {value} is not a valid response error.")
        return value

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return uuid.UUID(value)
            except ValueError:
                return value
        return value

    @field_validator("original_prompt_id", mode="before")
    @classmethod
    def _coerce_original_prompt_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return uuid.UUID(value)
        return value

    @field_validator("converter_identifiers", mode="before")
    @classmethod
    def _coerce_converter_identifiers(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return [ComponentIdentifier.from_dict(item) if isinstance(item, dict) else item for item in value]
        return value

    @field_validator(
        "prompt_target_identifier",
        "attack_identifier",
        "scorer_identifier",
        mode="before",
    )
    @classmethod
    def _coerce_component_identifier(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return ComponentIdentifier.from_dict(value)
        return value

    @field_validator("scores", mode="before")
    @classmethod
    def _coerce_scores(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return [Score.from_dict(item) if isinstance(item, dict) else item for item in value]
        return value

    # ------------------------------------------------------------------ #
    # Serializers that preserve the legacy ``to_dict`` JSON shape.
    # ------------------------------------------------------------------ #
    @field_serializer("id", "original_prompt_id")
    def _serialize_uuid(self, value: Optional[Union[uuid.UUID, str]]) -> Optional[str]:
        return str(value) if value is not None else None

    @field_serializer("timestamp")
    def _serialize_timestamp(self, value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value is not None else None

    @field_serializer("converter_identifiers")
    def _serialize_converter_identifiers(self, value: list[ComponentIdentifier]) -> list[dict[str, Any]]:
        return [item.to_dict() for item in value]

    @field_serializer("prompt_target_identifier", "attack_identifier", "scorer_identifier")
    def _serialize_component_identifier(
        self, value: Optional[ComponentIdentifier]
    ) -> Optional[dict[str, Any]]:
        return value.to_dict() if value is not None else None

    @field_serializer("targeted_harm_categories")
    def _serialize_targeted_harm_categories(self, value: list[str]) -> Optional[list[str]]:
        return value if value else None

    @field_serializer("scores")
    def _serialize_scores(self, value: list[Score]) -> list[dict[str, Any]]:
        return [score.to_dict() for score in value]

    # ------------------------------------------------------------------ #
    # Public API.
    # ------------------------------------------------------------------ #
    @property
    def _role(self) -> ChatMessageRole:
        """Backwards-compatible accessor for the role field."""
        return self.role

    @_role.setter
    def _role(self, value: ChatMessageRole) -> None:
        object.__setattr__(self, "role", value)

    @property
    def api_role(self) -> ChatMessageRole:
        """
        Role to use for API calls.

        Maps simulated_assistant to assistant for API compatibility.
        Use this property when sending messages to external APIs.
        """
        return "assistant" if self.role == "simulated_assistant" else self.role

    @property
    def is_simulated(self) -> bool:
        """
        Check if this is a simulated assistant response.

        Simulated responses come from prepended conversations or generated
        simulated conversations, not from actual target responses.
        """
        return self.role == "simulated_assistant"

    def get_role_for_storage(self) -> ChatMessageRole:
        """
        Get the actual stored role, including simulated_assistant.

        Use this when duplicating messages or preserving role information
        for storage. For API calls or comparisons, use api_role instead.

        Returns:
            The actual role stored (may be simulated_assistant).

        """
        return self.role

    def to_message(self) -> Message:
        """
        Convert this message piece into a Message.

        Returns:
            Message: A Message containing this piece.
        """
        from pyrit.models.message import Message

        return Message([self])

    def has_error(self) -> bool:
        """
        Check if the message piece has an error.

        Returns:
            bool: True when the response_error is not "none".

        """
        return self.response_error != "none"

    def is_blocked(self) -> bool:
        """
        Check if the message piece is blocked.

        Returns:
            bool: True when the response_error is "blocked".

        """
        return self.response_error == "blocked"

    async def set_sha256_values_async(self) -> None:
        """
        Compute SHA256 hash values for original and converted payloads.
        It should be called after object creation if `original_value` and `converted_value` are set.

        Note, this method is async due to the blob retrieval. And because of that, we opted
        to take it out of main and setter functions. The disadvantage is that it must be explicitly called.
        """
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

    def copy_lineage_from(self, source: MessagePiece) -> None:
        """
        Copy lineage metadata from ``source`` onto this piece.

        Lineage fields are the metadata that tie a piece back to its originating
        conversation, attack, and target. Mutable containers (``labels``,
        ``prompt_metadata``) are shallow-copied so that mutations on one piece
        do not affect others.

        Args:
            source: The piece whose lineage metadata is authoritative.
        """
        from pyrit.models.helpers.message_piece import copy_lineage_to

        copy_lineage_to(target=self, source=source)

    def set_piece_not_in_database(self) -> None:
        """
        Set that the prompt is not in the database.

        This is needed when we're scoring prompts or other things that have not been sent by PyRIT
        """
        from pyrit.models.helpers.message_piece import mark_not_persisted

        mark_not_persisted(self)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert this message piece to a dictionary representation.

        Returns:
            dict[str, Any]: Dictionary representation suitable for serialization.

        """
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessagePiece:
        """
        Reconstruct a MessagePiece from a dictionary.

        Args:
            data: Dictionary as produced by :meth:`to_dict`.

        Returns:
            MessagePiece: Reconstructed instance.
        """
        return cls.model_validate(data, context={"suppress_deprecation_warnings": True})

    def __str__(self) -> str:
        """
        Return a concise string representation of this message piece.

        Returns:
            str: Target, role, and converted value summary.

        """
        target_str = self.prompt_target_identifier.class_name if self.prompt_target_identifier else "Unknown"
        return f"{target_str}: {self.role}: {self.converted_value}"

    __repr__ = __str__

    def __eq__(self, other: object) -> bool:
        """
        Compare this message piece with another for semantic equality.

        Args:
            other (object): Object to compare.

        Returns:
            bool: True when all relevant message fields match.

        """
        if not isinstance(other, MessagePiece):
            return NotImplemented
        return (
            self.id == other.id
            and self.role == other.role
            and self.original_value == other.original_value
            and self.original_value_data_type == other.original_value_data_type
            and self.original_value_sha256 == other.original_value_sha256
            and self.converted_value == other.converted_value
            and self.converted_value_data_type == other.converted_value_data_type
            and self.converted_value_sha256 == other.converted_value_sha256
            and self.conversation_id == other.conversation_id
            and self.sequence == other.sequence
        )

    __hash__ = None  # type: ignore[assignment]


def sort_message_pieces(message_pieces: list[MessagePiece]) -> list[MessagePiece]:
    """
    Group by conversation_id.
    Order conversations by the earliest timestamp within each conversation_id.
    Within each conversation, order messages by sequence.

    Args:
        message_pieces (list[MessagePiece]): Message pieces to sort.

    Returns:
        list[MessagePiece]: Sorted message pieces.

    """
    earliest_timestamps = {
        convo_id: min(x.timestamp for x in message_pieces if x.conversation_id == convo_id)
        for convo_id in {x.conversation_id for x in message_pieces}
    }

    # Sort using the precomputed timestamp values, then by sequence
    return sorted(message_pieces, key=lambda x: (earliest_timestamps[x.conversation_id], x.conversation_id, x.sequence))
