# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.literals import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    ChatMessageRole,
    PromptDataType,
    PromptResponseError,
)
from pyrit.models.score import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    ComponentIdentifierField,
)

if TYPE_CHECKING:
    from pyrit.models.messages.message import Message


# Deprecated kwargs whose presence in ``MessagePiece(...)`` should emit a
# ``DeprecationWarning``. Each entry is ``(kwarg_name, removed_in)``. Kept here
# (rather than embedded in the validator body) to make the deprecation surface
# easy to read and update.
#
# These can be deleted entirely once their ``removed_in`` releases ship — the
# Pydantic field definitions and ``extra="forbid"`` config will then reject
# the kwargs naturally.
_DEPRECATED_KWARGS: tuple[tuple[str, str], ...] = (("labels", "0.16.0"),)


# ``ComponentIdentifierField`` is imported from ``pyrit.models.score`` above.
# It round-trips through the flat dict storage shape via its own Pydantic
# serializer, so no local annotated alias is needed here.


class MessagePiece(BaseModel):
    """
    A single piece of a message exchanged with a target.

    Targets that accept multimodal input (e.g., text + image) are represented
    as a list of ``MessagePiece`` instances grouped under one
    ``Message``.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=False,
    )

    id: uuid.UUID = Field(default_factory=uuid4)
    role: ChatMessageRole
    conversation_id: str | None = None
    sequence: int = -1
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    original_value: str
    original_value_data_type: PromptDataType = "text"
    original_value_sha256: str | None = None
    converted_value: str = ""
    converted_value_data_type: PromptDataType = "text"
    converted_value_sha256: str | None = None
    response_error: PromptResponseError = "none"
    original_prompt_id: uuid.UUID | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    prompt_metadata: dict[str, Any] = Field(default_factory=dict)
    converter_identifiers: list[ComponentIdentifierField] = Field(default_factory=list)

    # When True, the memory layer skips persisting this piece. Used for ephemeral
    # pieces a scorer creates to score arbitrary content; ``exclude=True`` keeps
    # the flag out of JSON / memory schema serialization. Named ``not_in_memory``
    # to match PyRIT's ``add_*_to_memory`` API verbs.
    not_in_memory: bool = Field(default=False, exclude=True)

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #
    @model_validator(mode="before")
    @classmethod
    def _warn_on_deprecated_kwargs(cls, data: Any) -> Any:
        """
        Emit DeprecationWarning for each deprecated kwarg explicitly passed.

        Only a truthy value counts as "passed". An empty/falsy value (e.g.
        ``labels={}``, the field default) is treated as not supplied, so callers
        that forward ``labels=<source>.labels`` on the happy path do not trip a
        spurious warning. This matches the post-construction assignment pattern
        used elsewhere (``piece.labels = labels`` guarded by ``if labels:``).

        Returns:
            The (unchanged) input ``data`` so validation can continue.
        """
        if not isinstance(data, dict):
            return data
        for kwarg, removed_in in _DEPRECATED_KWARGS:
            if data.get(kwarg):
                print_deprecation_message(
                    old_item=f"MessagePiece(..., {kwarg}=...)",
                    new_item="MessagePiece(...)",
                    removed_in=removed_in,
                )
        return data

    @model_validator(mode="before")
    @classmethod
    def _mirror_original_to_converted(cls, data: Any) -> Any:
        """
        When ``converted_value`` / ``converted_value_data_type`` aren't supplied, mirror the originals.

        Returns:
            The input ``data`` with mirrored converted fields applied.
        """
        if not isinstance(data, dict):
            return data
        if not data.get("converted_value") and "original_value" in data:
            data["converted_value"] = data["original_value"]
        if not data.get("converted_value_data_type") and "original_value_data_type" in data:
            data["converted_value_data_type"] = data["original_value_data_type"]
        return data

    @model_validator(mode="after")
    def _set_original_prompt_id_default(self) -> MessagePiece:
        """
        Enforce invariant: ``original_prompt_id == id`` for non-duplicate pieces.

        Returns:
            ``self`` (with ``original_prompt_id`` populated when previously ``None``).
        """
        if self.original_prompt_id is None:
            self.original_prompt_id = self.id
        return self

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def api_role(self) -> ChatMessageRole:
        """
        Role to use for API calls.

        Maps ``simulated_assistant`` to ``assistant`` for API compatibility.
        Use this property when sending messages to external APIs.
        """
        return "assistant" if self.role == "simulated_assistant" else self.role

    @property
    def is_simulated(self) -> bool:
        """Whether this piece represents a simulated assistant response."""
        return self.role == "simulated_assistant"

    def to_message(self) -> Message:
        """
        Wrap this piece in a single-piece ``Message``.

        Returns:
            A new ``Message`` containing only this piece.
        """
        # Deferred import: ``pyrit.models.messages.message`` imports ``MessagePiece``
        # at module load, so a top-level import here would deadlock the cycle.
        from pyrit.models.messages.message import Message

        return Message(message_pieces=[self])

    def copy_lineage_from(self, *, source: MessagePiece) -> None:
        """
        Copy lineage metadata from ``source`` onto this piece.

        Lineage fields are the metadata that tie a piece back to its originating
        conversation. Mutable containers (``labels``,
        ``prompt_metadata``) are shallow-copied so that mutations on one piece
        do not affect others.

        Args:
            source: The piece whose lineage will be copied onto ``self``.
        """
        self.conversation_id = source.conversation_id
        self.labels = dict(source.labels)
        self.prompt_metadata = dict(source.prompt_metadata)

    def has_error(self) -> bool:
        """
        Return ``True`` when ``response_error`` is not ``"none"``.

        Returns:
            ``True`` if the piece carries any non-``"none"`` error code.
        """
        return self.response_error != "none"

    def is_blocked(self) -> bool:
        """
        Return ``True`` when ``response_error`` is ``"blocked"``.

        Returns:
            ``True`` if the response was blocked by the target / content filter.
        """
        return self.response_error == "blocked"

    # ------------------------------------------------------------------ #
    # Adversarial placeholder support
    # ------------------------------------------------------------------ #
    @classmethod
    def adversarial_placeholder(cls, *, role: ChatMessageRole = "user") -> MessagePiece:
        """
        Build a placeholder text piece that signals the adversarial chat will
        generate the text content at this position.

        Intended for use inside ``AttackParameters.next_message`` when combining
        a user-supplied seed (e.g. a base image to edit) with adversarial-generated
        text on turn 1 of a multi-turn attack. A consumer that walks the message
        pieces can call ``is_adversarial_placeholder`` to detect a slot and
        replace its value with the generated text before sending the message.

        Args:
            role: The chat role to assign to the piece. Defaults to ``"user"``.

        Returns:
            A text ``MessagePiece`` flagged via ``prompt_metadata``.
        """
        return cls(
            role=role,
            original_value="",
            original_value_data_type="text",
            prompt_metadata={"adversarial_placeholder": True},
        )

    def is_adversarial_placeholder(self) -> bool:
        """
        Return ``True`` when this piece is a placeholder for adversarial-generated text.

        Detection is based on the ``adversarial_placeholder`` flag set by
        ``adversarial_placeholder``. Plain pieces (created without the flag)
        always return ``False``.

        Returns:
            ``True`` if this piece should be filled in by an adversarial chat.
        """
        return bool(self.prompt_metadata.get("adversarial_placeholder"))

    # ------------------------------------------------------------------ #
    # Deprecated method shims (removed in 0.16.0)
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """
        Return a JSON-mode dict representation (DEPRECATED — use ``model_dump``).

        Returns:
            A JSON-mode dict representation of the piece (same as
            ``self.model_dump(mode="json")``).
        """
        print_deprecation_message(
            old_item="MessagePiece.to_dict()",
            new_item='MessagePiece.model_dump(mode="json")',
            removed_in="0.16.0",
        )
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessagePiece:
        """
        Construct a MessagePiece from a dict (DEPRECATED — use ``model_validate``).

        Args:
            data: A dict matching the MessagePiece field schema.

        Returns:
            A new ``MessagePiece`` (same as ``cls.model_validate(data)``).
        """
        print_deprecation_message(
            old_item="MessagePiece.from_dict()",
            new_item="MessagePiece.model_validate()",
            removed_in="0.16.0",
        )
        return cls.model_validate(data)

    def set_piece_not_in_database(self) -> None:
        """
        Mark this piece as ephemeral (DEPRECATED — set ``not_in_memory`` directly).

        Example::

            piece.not_in_memory = True
        """
        print_deprecation_message(
            old_item="MessagePiece.set_piece_not_in_database()",
            new_item="MessagePiece.not_in_memory = True",
            removed_in="0.16.0",
        )
        self.not_in_memory = True

    async def set_sha256_values_async(self) -> None:
        """
        Compute SHA256 hash values for original and converted payloads.

        .. deprecated:: 0.15.0
            Use ``pyrit.memory.storage.serializers.set_message_piece_sha256_async`` instead.
            This method will be removed in 0.17.0.
        """
        import importlib

        print_deprecation_message(
            old_item="pyrit.models.messages.message_piece.MessagePiece.set_sha256_values_async",
            new_item="pyrit.memory.storage.serializers.set_message_piece_sha256_async",
            removed_in="0.17.0",
        )
        serializers = importlib.import_module("pyrit.memory.storage.serializers")
        await serializers.set_message_piece_sha256_async(self)


def sort_message_pieces(message_pieces: list[MessagePiece]) -> list[MessagePiece]:
    """
    Group by ``conversation_id``, ordering by earliest timestamp then ``sequence``.

    Conversations are ordered by their earliest piece's timestamp; pieces
    within a conversation are ordered by ``sequence``.

    Args:
        message_pieces: The pieces to sort. Not mutated.

    Returns:
        A new list containing the same pieces in deterministic order.
    """
    earliest_timestamps = {
        convo_id: min(x.timestamp for x in message_pieces if x.conversation_id == convo_id)
        for convo_id in {x.conversation_id for x in message_pieces}
    }
    return sorted(
        message_pieces,
        key=lambda x: (earliest_timestamps[x.conversation_id], x.conversation_id or "", x.sequence),
    )
