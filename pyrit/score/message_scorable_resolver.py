# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.models import (
    ContentEntryScorable,
    ContentScorable,
    Message,
    MessagePiece,
    MessageScorable,
    Scorable,
    group_message_pieces_into_conversations,
)

if TYPE_CHECKING:
    from pyrit.memory import MemoryInterface


class MessageScorableResolver:
    """Acquire message-shaped evidence for a ``MessageScorer``."""

    def resolve(self, *, scorable: Scorable, memory: MemoryInterface) -> Message:
        """
        Resolve supported scorables to the message view consumed by message scorers.

        Args:
            scorable (Scorable): A message reference, loose content, or a reference to
                loose content already stored in memory.
            memory (MemoryInterface): Memory used to resolve references.

        Returns:
            Message: The message view to score.

        Raises:
            TypeError: If the scorable is not message-shaped.
            ValueError: If referenced pieces or content are missing, or the pieces do not
                form one message.
        """
        if isinstance(scorable, MessageScorable):
            return self._resolve_message_reference(scorable=scorable, memory=memory)
        if isinstance(scorable, ContentEntryScorable):
            return self._adapt_content(scorable=self._resolve_content_reference(scorable=scorable, memory=memory))
        if isinstance(scorable, ContentScorable):
            return self._adapt_content(scorable=scorable)
        raise TypeError(
            f"Message scorers cannot score {type(scorable).__name__}. Pass a MessageScorable or a ContentScorable."
        )

    @staticmethod
    def _resolve_content_reference(*, scorable: ContentEntryScorable, memory: MemoryInterface) -> ContentScorable:
        content = memory.get_scorable_content(content_ids=[scorable.content_id]).get(scorable.content_id)
        if content is None:
            raise ValueError(f"No stored scorable content found for id {scorable.content_id}.")
        return content

    @staticmethod
    def _resolve_message_reference(*, scorable: MessageScorable, memory: MemoryInterface) -> Message:
        pieces = memory.get_message_pieces(prompt_ids=list(scorable.message_piece_ids))
        wanted = {str(piece_id) for piece_id in scorable.message_piece_ids}
        pieces = [piece for piece in pieces if str(piece.id) in wanted]
        found = {str(piece.id) for piece in pieces}
        missing = [str(piece_id) for piece_id in scorable.message_piece_ids if str(piece_id) not in found]
        if missing:
            raise ValueError(f"No message pieces found in memory for ids {missing}.")

        conversations = group_message_pieces_into_conversations(pieces)
        messages = [message for conversation in conversations for message in conversation]
        if len(messages) != 1:
            raise ValueError(
                f"Expected the referenced pieces to form exactly one message, got {len(messages)}. "
                "Reference pieces from a single message."
            )

        resolved = messages[0]
        by_id = {str(piece.id): piece for piece in resolved.message_pieces}
        resolved.message_pieces = [by_id[str(piece_id)] for piece_id in scorable.message_piece_ids]
        return resolved

    @staticmethod
    def _adapt_content(*, scorable: ContentScorable) -> Message:
        piece = MessagePiece(
            role="user",
            original_value=scorable.value,
            converted_value=scorable.value,
            original_value_data_type=scorable.data_type,
            converted_value_data_type=scorable.data_type,
        )
        piece.not_in_memory = True
        return piece.to_message()
