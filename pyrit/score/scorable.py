# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid  # noqa: TC003  (runtime-required by dataclass field annotations)
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.models import (  # noqa: TC001  (runtime-required by dataclass field annotations)
    ChatMessageRole,
    Message,
    MessagePiece,
    PromptDataType,
    group_message_pieces_into_conversations,
)

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface


class Scorable(ABC):  # noqa: B024  root type; each scorer family declares its own contract
    """
    What a scorer looks at.

    A scorable is normally a reference: it names the evidence instead of carrying it, and
    the scorer resolves it. ``ContentScorable`` is the exception, because loose content has
    nothing behind it to point at. Each scorer accepts the scorable kinds it supports and
    rejects the rest.
    """


@dataclass(frozen=True, kw_only=True)
class SingleMessageScorable(Scorable, ABC):
    """
    A scorable that names exactly one message that already exists.

    The message is in hand or in memory, so it has a real role and a real error state and
    the filters below mean something. Subclasses resolve themselves, but only identity
    resolution belongs here — returning the message the scorable names. A scorer that
    derives a different kind of evidence from the same reference, such as trace ids or a
    file write, does that widening itself.
    """

    role_filter: ChatMessageRole | None = None
    skip_on_error_result: bool = False

    @abstractmethod
    def resolve_message(self, *, memory: MemoryInterface) -> Message:
        """
        Return the message this scorable names.

        Args:
            memory (MemoryInterface): Memory to resolve references against.

        Returns:
            Message: The message to score.
        """


@dataclass(frozen=True, kw_only=True)
class MessageScorable(SingleMessageScorable):
    """
    A message the caller already holds.

    Use this when the message is in hand — an attack scoring the response it just
    received, or a caller scoring a message whose pieces were never persisted. Use
    ``MessageReferenceScorable`` when only the piece ids are known.
    """

    message: Message

    def resolve_message(self, *, memory: MemoryInterface) -> Message:
        """
        Return the message the caller supplied.

        Args:
            memory (MemoryInterface): Unused; the message is already in hand.

        Returns:
            Message: The message to score.
        """
        return self.message


@dataclass(frozen=True, kw_only=True)
class MessageReferenceScorable(SingleMessageScorable):
    """
    Specific message pieces, resolved from memory by id.

    Use this when the pieces are persisted and only their ids are known. Use
    ``MessageScorable`` when the caller already holds the message.
    """

    message_piece_ids: tuple[uuid.UUID | str, ...]

    def resolve_message(self, *, memory: MemoryInterface) -> Message:
        """
        Return the single message the referenced pieces form.

        Args:
            memory (MemoryInterface): Memory holding the pieces.

        Returns:
            Message: The message to score.

        Raises:
            ValueError: If any referenced piece is not in memory, or the pieces do not form
                exactly one message.
        """
        pieces = memory.get_message_pieces(prompt_ids=list(self.message_piece_ids))
        found = {str(piece.id) for piece in pieces}
        missing = [str(piece_id) for piece_id in self.message_piece_ids if str(piece_id) not in found]
        if missing:
            raise ValueError(f"No message pieces found in memory for ids {missing}.")

        conversations = group_message_pieces_into_conversations(pieces)
        messages = [message for conversation in conversations for message in conversation]
        if len(messages) != 1:
            raise ValueError(
                f"Expected the referenced pieces to form exactly one message, got {len(messages)}. "
                "Reference pieces from a single message."
            )
        return messages[0]


@dataclass(frozen=True, kw_only=True)
class ContentScorable(Scorable):
    """
    Loose content with no conversation behind it.

    This names content, not a message, so it is not a ``SingleMessageScorable``: there is no
    role and no error state, and the filters those scorables carry would be meaningless here.
    A message scorer adapts it with ``to_ephemeral_message``.
    """

    value: str
    data_type: PromptDataType = "text"

    def to_ephemeral_message(self) -> Message:
        """
        Wrap the content as a message so a message scorer can read it.

        This is an adapter, not a resolution: no such message exists until this call builds
        one, and it is marked as never persisted. Phase 2 stores loose content as a row of
        its own and retires this.

        Returns:
            Message: A throwaway message holding the content.
        """
        piece = MessagePiece(
            role="user",
            original_value=self.value,
            original_value_data_type=self.data_type,
        )
        piece.not_in_memory = True
        return Message(message_pieces=[piece])
