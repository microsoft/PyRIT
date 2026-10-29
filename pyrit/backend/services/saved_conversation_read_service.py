# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded, read-only saved conversation views shared by attack and scenario APIs."""

import uuid

from pyrit.backend.mappers._preview import format_last_message_preview
from pyrit.backend.mappers.attack_mappers import pyrit_messages_to_dto_async
from pyrit.backend.models.attacks import (
    AttackConversationsResponse,
    ConversationMessagesResponse,
)
from pyrit.backend.models.common import PaginationInfo
from pyrit.common.pagination import decode_keyset_cursor, encode_keyset_cursor, fingerprint_filters
from pyrit.memory import MemoryInterface
from pyrit.memory.memory_interface import MessagePieceKeysetCursor
from pyrit.models import ConversationStats, ConversationSummary


class SavedConversationReadService:
    """Read canonical saved references/messages without constructing an executable attack."""

    def __init__(self, *, memory: MemoryInterface) -> None:
        """Bind all saved reads to the caller's memory instance."""
        self._memory = memory

    async def list_conversations_async(
        self, *, attack_result_id: str, limit: int = 50, cursor: str | None = None
    ) -> AttackConversationsResponse | None:
        """
        Page saved main and pruned conversations without loading their message bodies.

        Returns:
            AttackConversationsResponse | None: A reference page, or None for an absent attack.

        Raises:
            ValueError: If the cursor belongs to another attack or an obsolete reference ordering.
        """
        page = await self._memory.get_attack_conversation_page_async(attack_result_id=attack_result_id, limit=limit)
        if page is None:
            return None
        fingerprint = fingerprint_filters(
            filters={
                "kind": "saved-conversations",
                "attack_result_id": str(uuid.UUID(attack_result_id)),
                "revision": page.reference_revision,
            }
        )
        anchor = decode_keyset_cursor(cursor=cursor, fingerprint=fingerprint)
        if cursor is not None and (
            anchor is None
            or anchor.position is None
            or anchor.position < 0
            or anchor.position >= page.total_count
            or anchor.identifier != str(uuid.UUID(attack_result_id))
            or anchor.timestamp != page.timestamp
        ):
            raise ValueError("Invalid or stale saved-conversation cursor.")
        offset = anchor.position if anchor is not None and anchor.position is not None else 0
        if offset:
            revision = page.reference_revision
            page = await self._memory.get_attack_conversation_page_async(
                attack_result_id=attack_result_id, offset=offset, limit=limit
            )
            if page is None:
                return None
            if page.reference_revision != revision:
                raise ValueError("Saved conversation references changed; restart pagination.")
        stats = (
            await self._memory.get_conversation_stats_async(
                conversation_ids=[reference.conversation_id for reference in page.conversations]
            )
            if page.conversations
            else {}
        )
        conversations = []
        for reference in page.conversations:
            stat = stats.get(reference.conversation_id, ConversationStats(message_count=0))
            conversations.append(
                ConversationSummary(
                    conversation_id=reference.conversation_id,
                    conversation_type=reference.conversation_type.value if reference.conversation_type else "main",
                    message_count=stat.message_count,
                    last_message_preview=format_last_message_preview(
                        value=stat.last_message_preview, data_type=stat.last_message_data_type
                    ),
                    created_at=stat.created_at,
                )
            )
        next_offset = offset + len(conversations)
        has_more = next_offset < page.total_count
        next_cursor = (
            encode_keyset_cursor(
                timestamp=page.timestamp,
                identifier=attack_result_id,
                fingerprint=fingerprint,
                position=next_offset,
            )
            if has_more
            else None
        )
        return AttackConversationsResponse(
            attack_result_id=attack_result_id,
            main_conversation_id=page.main_conversation_id,
            conversations=conversations,
            total_count=page.total_count,
            pagination=PaginationInfo(limit=limit, has_more=has_more, next_cursor=next_cursor),
        )

    async def get_messages_async(
        self, *, attack_result_id: str, conversation_id: str, limit: int = 100, cursor: str | None = None
    ) -> ConversationMessagesResponse | None:
        """
        Page canonical messages for one selected main or pruned conversation.

        Returns:
            ConversationMessagesResponse | None: A piece-bounded page, or None for an absent attack.

        Raises:
            ValueError: If the conversation is unrelated or the continuation cursor is invalid.
        """
        references = await self._memory.get_attack_conversation_page_async(
            attack_result_id=attack_result_id, conversation_id=conversation_id, limit=1
        )
        if references is None:
            return None
        if not references.conversations:
            raise ValueError(f"Conversation '{conversation_id}' is not part of attack '{attack_result_id}'")
        fingerprint = fingerprint_filters(
            filters={
                "kind": "saved-message-pieces",
                "attack_result_id": str(uuid.UUID(attack_result_id)),
                "conversation_id": conversation_id,
            }
        )
        anchor = decode_keyset_cursor(cursor=cursor, fingerprint=fingerprint)
        if cursor is not None and (anchor is None or anchor.position is None):
            raise ValueError("Invalid saved-message cursor.")
        piece_cursor = (
            MessagePieceKeysetCursor(
                sequence=anchor.position, timestamp=anchor.timestamp, message_piece_id=anchor.identifier
            )
            if anchor is not None and anchor.position is not None
            else None
        )
        page = await self._memory.get_conversation_message_page_async(
            conversation_id=conversation_id, cursor=piece_cursor, limit=limit
        )
        next_cursor = (
            encode_keyset_cursor(
                timestamp=page.next_cursor.timestamp,
                identifier=page.next_cursor.message_piece_id,
                position=page.next_cursor.sequence,
                fingerprint=fingerprint,
            )
            if page.next_cursor is not None
            else None
        )
        return ConversationMessagesResponse(
            conversation_id=conversation_id,
            messages=await pyrit_messages_to_dto_async(
                list(page.messages), memory=self._memory, objective_score_id=references.objective_score_id
            ),
            pagination=PaginationInfo(limit=limit, has_more=next_cursor is not None, next_cursor=next_cursor),
            partial_message_sequences=list(page.partial_message_sequences),
        )
