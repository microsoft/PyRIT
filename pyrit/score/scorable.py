# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid  # noqa: TC003  (runtime-required by dataclass field annotations)
from abc import ABC
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
class MessageScorable(Scorable):
    """
    Specific message pieces, resolved from memory by id.

    This names one message, or a subset of its pieces. Loose content that was never
    persisted has no ids to name, so it is a ``ContentScorable`` instead.
    """

    message_piece_ids: tuple[uuid.UUID | str, ...]

    # The design proposal puts role_filter on ConversationScorable only and leaves this
    # open as its phase-1 decision, on the grounds that naming pieces is already a
    # selection and filtering them again is a second one. Attacks do filter a named
    # response by role today, so both fields stay here until ConversationScorable arrives
    # and can own the selection instead.
    role_filter: ChatMessageRole | None = None
    skip_on_error_result: bool = False

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
        wanted = {str(piece_id) for piece_id in self.message_piece_ids}
        # Only the named pieces count, whatever else the filter happened to return.
        pieces = [piece for piece in pieces if str(piece.id) in wanted]
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

        # Memory returns pieces in its own order, but the scorable names an ordered tuple,
        # and a multi-piece message reads differently if its pieces are shuffled.
        resolved = messages[0]
        by_id = {str(piece.id): piece for piece in resolved.message_pieces}
        resolved.message_pieces = [by_id[str(piece_id)] for piece_id in self.message_piece_ids]
        return resolved

    @classmethod
    def from_message(
        cls,
        message: Message,
        *,
        role_filter: ChatMessageRole | None = None,
        skip_on_error_result: bool = False,
    ) -> MessageScorable:
        """
        Name the pieces of a persisted message.

        A scorable is a reference, so a message in hand is scored by naming its pieces
        rather than by travelling through the call. Use ``scorable_from_message`` instead
        when the message may not be persisted.

        Args:
            message (Message): The message whose pieces to name.
            role_filter (ChatMessageRole | None): Only score the message when it has this role.
            skip_on_error_result (bool): Skip scoring when the message is an error result.

        Returns:
            MessageScorable: A scorable naming the message's pieces.
        """
        return cls(
            message_piece_ids=tuple(piece.id for piece in message.message_pieces),
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result,
        )


@dataclass(frozen=True, kw_only=True)
class ContentScorable(Scorable):
    """
    Loose content with no conversation behind it.

    This names content, not a message: there is no role and no error state, so the filters
    a ``MessageScorable`` carries would be meaningless here. A message scorer adapts it
    with ``to_ephemeral_message``.
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

    @classmethod
    def from_message(cls, message: Message) -> ContentScorable:
        """
        Take the content out of a message that was never persisted.

        A message the caller built by hand has no ids to name, so it is content rather than
        a reference. Only the first piece is taken, because loose content is a single value.

        Args:
            message (Message): The unpersisted message whose content to take.

        Returns:
            ContentScorable: A scorable holding the message's content.
        """
        piece = message.get_piece()
        return cls(value=piece.original_value, data_type=piece.original_value_data_type)
