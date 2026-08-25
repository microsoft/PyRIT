# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid  # noqa: TC003  (runtime-required by Pydantic field annotations)
from abc import ABC
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from pyrit.models.literals import PromptDataType  # noqa: TC001  (runtime-required by Pydantic field annotations)

if TYPE_CHECKING:
    from pyrit.models.messages.message import Message


class Scorable(BaseModel, ABC):  # noqa: B024  root type; each scorer family declares its own contract
    """
    What a scorer looks at.

    A scorable is normally an inert reference: it names the evidence instead of carrying
    or acquiring it. ``ContentScorable`` is the exception, because loose content has
    nothing behind it to point at. A scorer-family resolver acquires the named evidence.

    A scorable that can be stored on a ``Score`` joins ``ScorableUnion`` and declares a
    ``scorable_type`` tag, which is what storage round-trips it on.
    """

    # Pydantic Baseclass overrride: we want frozen models and to forbid extras.
    model_config = ConfigDict(frozen=True, extra="forbid")


class MessageScorable(Scorable):
    """
    Specific message pieces, named by id.

    This names one message, or a subset of its pieces. Loose content that was never
    persisted has no ids to name, so it is a ``ContentScorable`` instead.
    """

    scorable_type: Literal["message"] = "message"
    message_piece_ids: tuple[uuid.UUID, ...]

    @model_validator(mode="after")
    def _validate_message_piece_ids(self) -> MessageScorable:
        """
        Reject id tuples that cannot name evidence.

        Returns:
            MessageScorable: ``self`` when validation passes.

        Raises:
            ValueError: If no ids are given, or if an id is repeated.
        """
        if not self.message_piece_ids:
            raise ValueError("A MessageScorable must name at least one message piece.")
        seen = [str(piece_id) for piece_id in self.message_piece_ids]
        if len(set(seen)) != len(seen):
            raise ValueError(f"A MessageScorable must name each message piece once, got {seen}.")
        return self

    @classmethod
    def from_message(
        cls,
        message: Message,
    ) -> MessageScorable:
        """
        Name the pieces of a persisted message.

        Args:
            message (Message): The message whose pieces to name.

        Returns:
            MessageScorable: A scorable naming the message's pieces.
        """
        return cls(message_piece_ids=tuple(piece.id for piece in message.message_pieces))


class ContentScorable(Scorable):
    """
    Loose content with no conversation behind it.

    This names content, not a message: there is no role or error state. A message-family
    resolver adapts it for existing message scorers. Once the content is persisted, the
    score anchors on a ``ContentEntryScorable`` naming the stored row instead.
    """

    scorable_type: Literal["content"] = "content"
    value: str
    data_type: PromptDataType = "text"

    @classmethod
    def from_message(cls, message: Message) -> ContentScorable:
        """
        Describe the converted content of a single-piece ephemeral message.

        Scorers consume ``converted_value``, so this adapter preserves the converted value
        and data type rather than the pre-conversion input. Everything else the message
        carried is dropped, including its role and its error state, so a scorer's
        deterministic blocked-response handling no longer applies. Use
        ``MessageScorer.score_message_async`` when that state is part of the evidence.

        Args:
            message (Message): The ephemeral message whose converted content to take.

        Returns:
            ContentScorable: A scorable holding the converted message content.
        """
        piece = message.get_piece()
        return cls(value=piece.converted_value, data_type=piece.converted_value_data_type)


class ContentEntryScorable(Scorable):
    """
    Persisted loose content, named by id.

    This is the stored anchor for a score about loose content: every scorable that reaches
    storage is a reference, so a persisted score never carries a payload.
    """

    scorable_type: Literal["content_entry"] = "content_entry"
    content_id: uuid.UUID
    data_type: PromptDataType = "text"


# Polymorphic union of scorables that can be stored on a Score. Every member declares a
# ``scorable_type`` tag and Pydantic dispatches on it, so a new member is never mistaken for
# an existing one and storage never depends on field shape.
ScorableUnion = Annotated[
    MessageScorable | ContentScorable | ContentEntryScorable,
    Field(discriminator="scorable_type"),
]

# Single source of truth for what a stored score can be anchored on, read back off the union
# so adding a member does not require a second edit here.
SCORABLE_TYPES: tuple[type[Scorable], ...] = get_args(get_args(ScorableUnion)[0])

_SCORABLE_ADAPTER: TypeAdapter[ScorableUnion] = TypeAdapter(ScorableUnion)


def scorable_from_dict(value: dict[str, Any]) -> ScorableUnion:
    """
    Rebuild a stored scorable from its ``scorable_type`` tag.

    Args:
        value (dict[str, Any]): A stored ``model_dump`` of a scorable.

    Returns:
        ScorableUnion: The rebuilt scorable.

    Raises:
        ValidationError: If the stored value names no known scorable.
    """
    return _SCORABLE_ADAPTER.validate_python(value)


def storable_scorable(scorable: Scorable | None) -> ScorableUnion | None:
    """
    Narrow a scorable to one a ``Score`` can be anchored on.

    Args:
        scorable (Scorable | None): The scorable to narrow.

    Returns:
        ScorableUnion | None: The scorable, or None when there was none to narrow.

    Raises:
        TypeError: If the scorable's kind has no storage shape. A stored score that silently
            dropped its anchor could not be audited, so an unsupported kind is refused rather
            than persisted without provenance.
    """
    if scorable is None:
        return None
    if isinstance(scorable, SCORABLE_TYPES):
        return cast("ScorableUnion", scorable)
    supported = ", ".join(sorted(kind.__name__ for kind in SCORABLE_TYPES))
    raise TypeError(
        f"{type(scorable).__name__} cannot anchor a stored score. Add it to ScorableUnion with its own "
        f"'scorable_type' tag. Storable scorables: {supported}."
    )
