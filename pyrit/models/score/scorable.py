# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid  # noqa: TC003  (runtime-required by dataclass field annotations)
from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models.literals import PromptDataType  # noqa: TC001  (runtime-required by dataclass field annotations)

if TYPE_CHECKING:
    from pyrit.models.messages.message import Message


class Scorable(ABC):  # noqa: B024  root type; each scorer family declares its own contract
    """
    What a scorer looks at.

    A scorable is normally an inert reference: it names the evidence instead of carrying
    or acquiring it. ``ContentScorable`` is the exception, because loose content has
    nothing behind it to point at. A scorer-family resolver acquires the named evidence.
    """


@dataclass(frozen=True, kw_only=True)
class MessageScorable(Scorable):
    """
    Specific message pieces, named by id.

    This names one message, or a subset of its pieces. Loose content that was never
    persisted has no ids to name, so it is a ``ContentScorable`` instead.
    """

    message_piece_ids: tuple[uuid.UUID | str, ...]

    def __post_init__(self) -> None:
        """
        Reject id tuples that cannot name evidence.

        Raises:
            ValueError: If no ids are given, or if an id is repeated.
        """
        if not self.message_piece_ids:
            raise ValueError("A MessageScorable must name at least one message piece.")
        seen = [str(piece_id) for piece_id in self.message_piece_ids]
        if len(set(seen)) != len(seen):
            raise ValueError(f"A MessageScorable must name each message piece once, got {seen}.")

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


@dataclass(frozen=True, kw_only=True)
class ContentScorable(Scorable):
    """
    Loose content with no conversation behind it.

    This names content, not a message: there is no role or error state. A message-family
    resolver adapts it for existing message scorers.
    """

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
