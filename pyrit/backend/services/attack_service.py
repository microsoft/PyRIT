# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Attack service for managing attacks.

All user interactions are modeled as "attacks" - this is the attack-centric API design.
Handles attack lifecycle, message sending, and scoring.

ARCHITECTURE:
- Each attack is represented by an AttackResult stored in the database
- The AttackResult has a conversation_id that links to the main conversation
- Messages are stored via PyRIT memory with that conversation_id
- For human-led attacks, it's a 1-to-1 mapping: one AttackResult, one conversation
- AI-generated attacks may have multiple related conversations
"""

import mimetypes
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional, cast
from urllib.parse import parse_qs, urlparse

from pyrit.backend.mappers.attack_mappers import (
    attack_result_to_summary,
    pyrit_messages_to_dto_async,
    request_piece_to_pyrit_message_piece,
    request_to_pyrit_message,
)
from pyrit.backend.models.attacks import (
    AddMessageRequest,
    AddMessageResponse,
    AttackConversationsResponse,
    AttackListResponse,
    AttackSummary,
    ConversationMessagesResponse,
    ConversationSummary,
    CreateAttackRequest,
    CreateAttackResponse,
    CreateConversationRequest,
    CreateConversationResponse,
    UpdateAttackRequest,
    UpdateMainConversationRequest,
    UpdateMainConversationResponse,
)
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.services.converter_service import get_converter_service
from pyrit.backend.services.target_service import get_target_service
from pyrit.identifiers import ComponentIdentifier
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ConversationStats,
    ConversationType,
    MessagePiece,
    PromptDataType,
    data_serializer_factory,
)
from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer


class AttackService:
    """
    Service for managing attacks.

    Uses PyRIT memory (database) as the source of truth via AttackResult.
    """

    def __init__(self) -> None:
        """Initialize the attack service."""
        self._memory = CentralMemory.get_memory_instance()

    # ========================================================================
    # Public API Methods
    # ========================================================================

    async def list_attacks_async(
        self,
        *,
        attack_type: Optional[str] = None,
        converter_types: Optional[list[str]] = None,
        outcome: Optional[Literal["undetermined", "success", "failure"]] = None,
        labels: Optional[dict[str, str]] = None,
        min_turns: Optional[int] = None,
        max_turns: Optional[int] = None,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> AttackListResponse:
        """
        List attacks with optional filtering and pagination.

        Queries AttackResult entries from the database.

        Args:
            attack_type: Filter by exact attack type name (case-sensitive).
            converter_types: Filter by converter usage.
                None = no filter, [] = only attacks with no converters,
                ["A", "B"] = only attacks using ALL specified converters (AND logic, case-insensitive).
            outcome: Filter by attack outcome.
            labels: Filter by labels (all must match).
            min_turns: Filter by minimum executed turns.
            max_turns: Filter by maximum executed turns.
            limit: Maximum items to return.
            cursor: Pagination cursor.

        Returns:
            AttackListResponse with filtered and paginated attack summaries.
        """
        # Phase 1: Query + lightweight filtering (no pieces needed)
        attack_results = self._memory.get_attack_results(
            outcome=outcome,
            labels=labels if labels else None,
            attack_class=attack_type,
            converter_classes=converter_types,
        )

        filtered: list[AttackResult] = []
        for ar in attack_results:
            if min_turns is not None and ar.executed_turns < min_turns:
                continue
            if max_turns is not None and ar.executed_turns > max_turns:
                continue
            filtered.append(ar)

        # Sort by most recent (metadata lives on AttackResult, no pieces needed)
        filtered.sort(
            key=lambda ar: ar.metadata.get("updated_at", ar.metadata.get("created_at", "")),
            reverse=True,
        )

        # Paginate on the lightweight list first
        page_results, has_more = self._paginate_attack_results(filtered, cursor, limit)
        next_cursor = page_results[-1].attack_result_id if has_more and page_results else None

        # Phase 2: Lightweight DB aggregation for the page only.
        # Collect conversation IDs we care about (main + pruned, not adversarial).
        all_conv_ids: set[str] = set()
        for ar in page_results:
            all_conv_ids.update(ar.get_active_conversation_ids())

        stats_map = self._memory.get_conversation_stats(conversation_ids=list(all_conv_ids)) if all_conv_ids else {}

        # Phase 3: Build summaries from aggregated stats for the page
        page: list[AttackSummary] = []
        for ar in page_results:
            # Merge stats for the main conversation and its pruned relatives.
            main_stats = stats_map.get(ar.conversation_id)
            pruned_ids = ar.get_pruned_conversation_ids()
            pruned_stats = [stats_map[cid] for cid in pruned_ids if cid in stats_map]

            total_count = (main_stats.message_count if main_stats else 0) + sum(s.message_count for s in pruned_stats)
            preview = main_stats.last_message_preview if main_stats else None
            conv_labels = (main_stats.labels if main_stats else None) or {}

            merged = ConversationStats(
                message_count=total_count,
                last_message_preview=preview,
                labels=conv_labels,
            )

            page.append(attack_result_to_summary(ar, stats=merged))

        return AttackListResponse(
            items=page,
            pagination=PaginationInfo(limit=limit, has_more=has_more, next_cursor=next_cursor, prev_cursor=cursor),
        )

    async def get_attack_options_async(self) -> list[str]:
        """
        Get all unique attack type names from stored attack results.

        Delegates to the memory layer which extracts distinct class_name
        values from the attack_identifier JSON column via SQL.

        Returns:
            Sorted list of unique attack type names.
        """
        return self._memory.get_unique_attack_class_names()

    async def get_converter_options_async(self) -> list[str]:
        """
        Get all unique converter type names used across attack results.

        Delegates to the memory layer which extracts distinct converter
        type names from the attack_identifier JSON column via SQL.

        Returns:
            Sorted list of unique converter type names.
        """
        return self._memory.get_unique_converter_class_names()

    async def get_attack_async(self, *, attack_result_id: str) -> Optional[AttackSummary]:
        """
        Get attack details (high-level metadata, no messages).

        Queries the AttackResult from the database by its primary key.

        Returns:
            AttackSummary if found, None otherwise.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            return None

        ar = results[0]
        stats_map = self._memory.get_conversation_stats(conversation_ids=[ar.conversation_id])
        stats = stats_map.get(ar.conversation_id, ConversationStats(message_count=0))
        return attack_result_to_summary(ar, stats=stats)

    async def get_conversation_messages_async(
        self,
        *,
        attack_result_id: str,
        conversation_id: str,
    ) -> Optional[ConversationMessagesResponse]:
        """
        Get all messages for a conversation belonging to an attack.

        Args:
            attack_result_id: The AttackResult's primary key (used to verify existence).
            conversation_id: The conversation whose messages to return.

        Returns:
            ConversationMessagesResponse if attack found, None otherwise.

        Raises:
            ValueError: If the conversation does not belong to the attack.
        """
        # Check attack exists
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            return None

        # Verify the conversation belongs to this attack
        ar = results[0]
        if conversation_id not in ar.get_active_conversation_ids():
            raise ValueError(f"Conversation '{conversation_id}' is not part of attack '{attack_result_id}'")

        # Get messages for this conversation
        pyrit_messages = self._memory.get_conversation(conversation_id=conversation_id)
        backend_messages = await pyrit_messages_to_dto_async(list(pyrit_messages))

        return ConversationMessagesResponse(
            conversation_id=conversation_id,
            messages=backend_messages,
        )

    async def create_attack_async(self, *, request: CreateAttackRequest) -> CreateAttackResponse:
        """
        Create a new attack.

        Creates an AttackResult with a new conversation_id.  When
        ``source_conversation_id`` and ``cutoff_index`` are provided the
        backend duplicates messages up to and including the cutoff turn,
        applies the new labels, and maps assistant roles to
        ``simulated_assistant`` so the branched context is inert.

        Returns:
            CreateAttackResponse with the new attack's ID and creation time.

        Raises:
            ValueError: If the target is not found.
        """
        target_service = get_target_service()
        target_instance = await target_service.get_target_async(target_registry_name=request.target_registry_name)
        if not target_instance:
            raise ValueError(f"Target instance '{request.target_registry_name}' not found")

        # Get the actual target object so we can capture its ComponentIdentifier
        target_obj = target_service.get_target_object(target_registry_name=request.target_registry_name)
        target_identifier = target_obj.get_identifier() if target_obj else None

        now = datetime.now(timezone.utc)

        # Merge source label with any user-supplied labels
        labels = dict(request.labels) if request.labels else {}
        labels.setdefault("source", "gui")

        # --- Branch via duplication (preferred for tracking) ---------------
        if request.source_conversation_id is not None and request.cutoff_index is not None:
            conversation_id = self._duplicate_conversation_up_to(
                source_conversation_id=request.source_conversation_id,
                cutoff_index=request.cutoff_index,
                labels_override=labels,
                remap_assistant_to_simulated=True,
            )
        else:
            conversation_id = str(uuid.uuid4())

        # Create AttackResult
        attack_result = AttackResult(
            conversation_id=conversation_id,
            objective=request.name or "Manual attack via GUI",
            atomic_attack_identifier=build_atomic_attack_identifier(
                attack_identifier=ComponentIdentifier(
                    class_name=request.name or "ManualAttack",
                    class_module="pyrit.backend",
                    children={"objective_target": target_identifier} if target_identifier else {},
                ),
            ),
            outcome=AttackOutcome.UNDETERMINED,
            metadata={
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )

        # Store in memory
        self._memory.add_attack_results_to_memory(attack_results=[attack_result])

        # Store prepended conversation messages if provided
        if request.prepended_conversation:
            await self._store_prepended_messages(
                conversation_id=conversation_id,
                prepended=request.prepended_conversation,
                labels=labels,
            )

        return CreateAttackResponse(
            attack_result_id=attack_result.attack_result_id,
            conversation_id=conversation_id,
            created_at=now,
        )

    async def update_attack_async(
        self, *, attack_result_id: str, request: UpdateAttackRequest
    ) -> Optional[AttackSummary]:
        """
        Update an attack's outcome.

        Updates the AttackResult in the database.

        Returns:
            Updated AttackSummary if found, None otherwise.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            return None

        # Map outcome
        outcome_map = {
            "undetermined": AttackOutcome.UNDETERMINED,
            "success": AttackOutcome.SUCCESS,
            "failure": AttackOutcome.FAILURE,
        }
        new_outcome = outcome_map.get(request.outcome, AttackOutcome.UNDETERMINED)

        ar = results[0]
        updated_metadata = dict(ar.metadata) if ar.metadata else {}
        updated_metadata["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields={
                "outcome": new_outcome.value,
                "attack_metadata": updated_metadata,
            },
        )

        return await self.get_attack_async(attack_result_id=attack_result_id)

    async def get_conversations_async(self, *, attack_result_id: str) -> Optional[AttackConversationsResponse]:
        """
        Get all conversations belonging to an attack.

        Includes the main conversation and all related conversations from the
        AttackResult. Each entry is enriched with message count, a preview,
        and the earliest message timestamp using a single batched query.

        Returns:
            AttackConversationsResponse if attack found, None otherwise.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            return None

        # attack_result_id is a unique primary key, so at most one result is returned.
        ar = results[0]

        # Collect all conversation IDs (main + PRUNED related) and fetch stats in one query.
        active_conv_ids = list(ar.get_active_conversation_ids())
        stats_map = self._memory.get_conversation_stats(conversation_ids=active_conv_ids)

        conversations: list[ConversationSummary] = []
        for conv_id in active_conv_ids:
            stats = stats_map.get(conv_id)
            created_at = stats.created_at if stats else None
            # SQLite returns naive datetimes — normalize to UTC (same pattern as _ensure_utc)
            if created_at is not None and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            conversations.append(
                ConversationSummary(
                    conversation_id=conv_id,
                    message_count=stats.message_count if stats else 0,
                    last_message_preview=stats.last_message_preview if stats else None,
                    created_at=created_at,
                )
            )

        # Sort conversations by created_at (earliest first). In-flight conversations
        # have no stored messages yet so created_at is None — treat them as the most
        # recent (they were just created) so they sort after older conversations
        # instead of jumping to an arbitrary position.
        now = datetime.now(timezone.utc)
        conversations.sort(key=lambda c: c.created_at or now)

        return AttackConversationsResponse(
            attack_result_id=attack_result_id,
            main_conversation_id=ar.conversation_id,
            conversations=conversations,
        )

    async def create_related_conversation_async(
        self, *, attack_result_id: str, request: CreateConversationRequest
    ) -> Optional[CreateConversationResponse]:
        """
        Create a new conversation within an existing attack.

        When ``source_conversation_id`` and ``cutoff_index`` are provided the
        backend duplicates messages up to and including the cutoff turn.  The
        duplication preserves ``original_prompt_id`` so that the new pieces
        remain linked to the originals for tracking purposes.

        Returns:
            CreateConversationResponse if attack found, None otherwise.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            return None

        ar = results[0]
        now = datetime.now(timezone.utc)

        # Validate that both or neither branching fields are provided
        if (request.source_conversation_id is None) != (request.cutoff_index is None):
            raise ValueError("Both source_conversation_id and cutoff_index must be provided together")

        # Validate source_conversation_id belongs to this attack
        if request.source_conversation_id is not None and not ar.includes_conversation(request.source_conversation_id):
            raise ValueError(
                f"Conversation '{request.source_conversation_id}' is not part of attack '{attack_result_id}'"
            )

        # --- Branch via duplication (preferred for tracking) ---------------
        if request.source_conversation_id is not None and request.cutoff_index is not None:
            new_conversation_id = self._duplicate_conversation_up_to(
                source_conversation_id=request.source_conversation_id,
                cutoff_index=request.cutoff_index,
            )
        else:
            new_conversation_id = str(uuid.uuid4())

        # Add to pruned_conversation_ids so user-created branches are visible in the GUI history panel.
        existing_pruned = ar.get_pruned_conversation_ids()

        updated_metadata = dict(ar.metadata or {})
        updated_metadata["updated_at"] = now.isoformat()

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields={
                "pruned_conversation_ids": existing_pruned + [new_conversation_id],
                "attack_metadata": updated_metadata,
            },
        )

        return CreateConversationResponse(conversation_id=new_conversation_id, created_at=now)

    async def update_main_conversation_async(
        self, *, attack_result_id: str, request: UpdateMainConversationRequest
    ) -> Optional[UpdateMainConversationResponse]:
        """
        Change the main conversation by promoting a related conversation.

        Updates the AttackResult's ``conversation_id`` to the target
        conversation and moves the previous main conversation into the
        related conversations list.  The ``attack_result_id`` (primary
        key) remains unchanged.

        Returns:
            UpdateMainConversationResponse if the source attack exists, None otherwise.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            return None

        ar = results[0]
        target_conv_id = request.conversation_id

        # If the target is already the main conversation, nothing to do.
        if target_conv_id == ar.conversation_id:
            return UpdateMainConversationResponse(
                attack_result_id=attack_result_id,
                conversation_id=target_conv_id,
                updated_at=datetime.now(timezone.utc),
            )

        # Verify the conversation belongs to this attack (main or related)
        if not ar.includes_conversation(target_conv_id):
            raise ValueError(f"Conversation '{target_conv_id}' is not part of this attack")

        # Build updated DB columns: remove target from its list, add old main
        # to pruned list (user-visible GUI conversations are PRUNED, not ADVERSARIAL).
        updated_pruned = [
            ref.conversation_id
            for ref in ar.related_conversations
            if ref.conversation_id != target_conv_id and ref.conversation_type == ConversationType.PRUNED
        ]
        updated_adversarial = [
            ref.conversation_id
            for ref in ar.related_conversations
            if ref.conversation_id != target_conv_id and ref.conversation_type == ConversationType.ADVERSARIAL
        ]
        # The old main becomes a pruned related conversation so it remains
        # visible in the GUI and fetchable via get_conversation_messages.
        updated_pruned.append(ar.conversation_id)

        now = datetime.now(timezone.utc)
        updated_metadata = dict(ar.metadata or {})
        updated_metadata["updated_at"] = now.isoformat()

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields={
                "conversation_id": target_conv_id,
                "pruned_conversation_ids": updated_pruned if updated_pruned else None,
                "adversarial_chat_conversation_ids": updated_adversarial if updated_adversarial else None,
                "attack_metadata": updated_metadata,
            },
        )

        return UpdateMainConversationResponse(
            attack_result_id=attack_result_id,
            conversation_id=target_conv_id,
            updated_at=now,
        )

    async def add_message_async(self, *, attack_result_id: str, request: AddMessageRequest) -> AddMessageResponse:
        """
        Add a message to an attack, optionally sending to target.

        Messages are stored in the database via PromptNormalizer.
        The ``request.target_conversation_id`` field specifies which conversation
        the messages are stored under (main conversation or a related one).

        Returns:
            AddMessageResponse containing the updated attack detail.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            raise ValueError(f"Attack '{attack_result_id}' not found")

        ar = results[0]
        main_conversation_id = ar.conversation_id

        self._validate_target_match(attack_identifier=ar.attack_identifier, request=request)
        self._validate_operator_match(conversation_id=main_conversation_id, request=request)

        msg_conversation_id = request.target_conversation_id

        # Validate the target conversation belongs to this attack (main + pruned only)
        if msg_conversation_id not in ar.get_active_conversation_ids():
            raise ValueError(f"Conversation '{msg_conversation_id}' is not part of attack '{attack_result_id}'")

        target_registry_name = request.target_registry_name
        if request.send and not target_registry_name:
            raise ValueError("target_registry_name is required when send=True")

        # Get existing messages to determine sequence.
        # NOTE: This read-then-write is not atomic (TOCTOU). Fine for the
        # current single-user UI, but would need a DB-level sequence
        # generator or optimistic locking if concurrent writes are supported.
        existing = self._memory.get_message_pieces(conversation_id=msg_conversation_id)
        sequence = max((p.sequence for p in existing), default=-1) + 1

        attack_labels = self._resolve_labels(
            conversation_id=msg_conversation_id,
            main_conversation_id=main_conversation_id,
            existing_pieces=existing,
            request_labels=request.labels,
        )

        if request.send:
            assert target_registry_name is not None  # validated above
            await self._send_and_store_message_async(
                conversation_id=msg_conversation_id,
                target_registry_name=target_registry_name,
                request=request,
                sequence=sequence,
                labels=attack_labels,
            )
        else:
            await self._store_message_only_async(
                conversation_id=msg_conversation_id,
                request=request,
                sequence=sequence,
                labels=attack_labels,
            )

        await self._update_attack_after_message_async(attack_result_id=attack_result_id, ar=ar, request=request)

        attack_detail = await self.get_attack_async(attack_result_id=attack_result_id)
        if attack_detail is None:
            raise ValueError(f"Attack '{attack_result_id}' not found after update")

        attack_messages = await self.get_conversation_messages_async(
            attack_result_id=attack_result_id,
            conversation_id=msg_conversation_id,
        )
        if attack_messages is None:
            raise ValueError(f"Attack '{attack_result_id}' messages not found after update")

        return AddMessageResponse(attack=attack_detail, messages=attack_messages)

    def _validate_target_match(
        self, *, attack_identifier: Optional[ComponentIdentifier], request: AddMessageRequest
    ) -> None:
        """
        Validate that the request target matches the attack's stored target.

        Raises:
            ValueError: If the target in the request doesn't match the attack's target.
        """
        if not request.send or not request.target_registry_name:
            return

        stored_target_id = attack_identifier.get_child("objective_target") if attack_identifier else None
        if not stored_target_id:
            return

        target_service = get_target_service()
        request_target_obj = target_service.get_target_object(target_registry_name=request.target_registry_name)
        if not request_target_obj:
            return

        request_target_id = request_target_obj.get_identifier()
        if (
            stored_target_id.class_name != request_target_id.class_name
            or (stored_target_id.params.get("endpoint") or "") != (request_target_id.params.get("endpoint") or "")
            or (stored_target_id.params.get("model_name") or "") != (request_target_id.params.get("model_name") or "")
        ):
            raise ValueError(
                f"Target mismatch: attack was created with "
                f"{stored_target_id.class_name}/{stored_target_id.params.get('model_name')} "
                f"but request uses "
                f"{request_target_id.class_name}/{request_target_id.params.get('model_name')}. "
                f"Create a new attack to use a different target."
            )

    def _validate_operator_match(self, *, conversation_id: str, request: AddMessageRequest) -> None:
        """
        Validate that the request operator matches existing messages' operator.

        Raises:
            ValueError: If the operator in the request doesn't match existing messages.
        """
        if not request.labels:
            return

        existing_pieces = self._memory.get_message_pieces(conversation_id=conversation_id)
        existing_operator = next(
            (p.labels.get("operator") for p in existing_pieces if p.labels and p.labels.get("operator")),
            None,
        )
        if not existing_operator:
            return

        request_operator = request.labels.get("operator")
        if request_operator and request_operator != existing_operator:
            raise ValueError(
                f"Operator mismatch: attack belongs to operator '{existing_operator}' "
                f"but request is from '{request_operator}'. "
                f"Create a new attack to continue."
            )

    def _resolve_labels(
        self,
        *,
        conversation_id: str,
        main_conversation_id: str,
        existing_pieces: Sequence[MessagePiece],
        request_labels: Optional[dict[str, str]],
    ) -> dict[str, str]:
        """
        Resolve labels for a new message by inheriting from existing pieces.

        Tries the target conversation first, falls back to the main conversation,
        then falls back to labels provided explicitly in the request.

        Returns:
            dict[str, str]: Resolved labels for the new message.
        """
        attack_labels: Optional[dict[str, str]] = next(
            (p.labels for p in existing_pieces if p.labels and len(p.labels) > 0), None
        )
        if not attack_labels:
            main_pieces = self._memory.get_message_pieces(conversation_id=main_conversation_id)
            attack_labels = next((p.labels for p in main_pieces if p.labels and len(p.labels) > 0), None)
        if not attack_labels:
            attack_labels = dict(request_labels) if request_labels else {}
        return attack_labels

    async def _update_attack_after_message_async(
        self, *, attack_result_id: str, ar: AttackResult, request: AddMessageRequest
    ) -> None:
        """
        Update attack metadata and converter tracking after a message is added.
        """
        updated_metadata = dict(ar.metadata or {})
        updated_metadata["updated_at"] = datetime.now(timezone.utc).isoformat()

        update_fields: dict[str, Any] = {"attack_metadata": updated_metadata}

        if request.converter_ids:
            converter_objs = get_converter_service().get_converter_objects_for_ids(converter_ids=request.converter_ids)
            new_converter_ids = [c.get_identifier() for c in converter_objs]
            aid = ar.attack_identifier
            if aid:
                existing_converters: list[ComponentIdentifier] = list(aid.get_child_list("request_converters"))
                existing_hashes = {c.hash for c in existing_converters}
                merged = existing_converters + [c for c in new_converter_ids if c.hash not in existing_hashes]
                new_children = dict(aid.children)
                if merged:
                    new_children["request_converters"] = merged
                new_aid = ComponentIdentifier(
                    class_name=aid.class_name,
                    class_module=aid.class_module,
                    params=dict(aid.params),
                    children=new_children,
                )
                update_fields["attack_identifier"] = new_aid.to_dict()

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields=update_fields,
        )

    # ========================================================================
    # Private Helper Methods - Pagination
    # ========================================================================

    def _paginate_attack_results(
        self, items: list[AttackResult], cursor: Optional[str], limit: int
    ) -> tuple[list[AttackResult], bool]:
        """
        Apply cursor-based pagination over AttackResult objects.

        Operates on lightweight AttackResult objects before pieces are fetched,
        so only the final page incurs per-attack piece queries.

        Returns:
            Tuple of (paginated items, has_more flag).
        """
        start_idx = 0
        if cursor:
            for i, item in enumerate(items):
                if item.attack_result_id == cursor:
                    start_idx = i + 1
                    break

        page = items[start_idx : start_idx + limit]
        has_more = len(items) > start_idx + limit
        return page, has_more

    # ========================================================================
    # Private Helper Methods - Duplicate / Branch
    # ========================================================================

    def _duplicate_conversation_up_to(
        self,
        *,
        source_conversation_id: str,
        cutoff_index: int,
        labels_override: Optional[dict[str, str]] = None,
        remap_assistant_to_simulated: bool = False,
    ) -> str:
        """
        Duplicate messages from a conversation up to and including a turn index.

        Uses the memory layer's ``duplicate_messages`` so that each new
        piece gets a fresh ``id`` and ``timestamp`` while preserving
        ``original_prompt_id`` for tracking lineage.

        Args:
            source_conversation_id: The conversation to copy from.
            cutoff_index: Include messages with sequence <= cutoff_index.
            labels_override: When provided, the duplicated pieces' labels are
                replaced with these values.  Used when branching into a new
                attack that belongs to a different operator.
            remap_assistant_to_simulated: When True, pieces with role
                ``assistant`` are changed to ``simulated_assistant`` so the
                branched context is inert and won't confuse the target.

        Returns:
            The new conversation ID containing the duplicated messages.
        """
        messages = self._memory.get_conversation(conversation_id=source_conversation_id)
        messages_to_copy = [m for m in messages if m.sequence <= cutoff_index]

        new_conversation_id, all_pieces = self._memory.duplicate_messages(messages=messages_to_copy)

        # Apply optional overrides to the fresh pieces before persisting
        for piece in all_pieces:
            if labels_override is not None:
                piece.labels = dict(labels_override)
            if remap_assistant_to_simulated and piece.role == "assistant":
                piece.role = "simulated_assistant"

        if all_pieces:
            self._memory.add_message_pieces_to_memory(message_pieces=list(all_pieces))

        return new_conversation_id

    # ========================================================================
    # Private Helper Methods - Store Messages
    # ========================================================================

    @staticmethod
    async def _persist_base64_pieces_async(request: AddMessageRequest) -> None:
        """
        Persist base64-encoded non-text pieces to disk, updating values in-place.

        The frontend sends binary media (images, audio, etc.) as base64 strings
        with a ``*_path`` data_type.  The PyRIT target layer expects ``*_path``
        values to be **file paths**, so we decode the base64 data, write it to
        the results store, and replace the request values with the resulting
        file path before the message is built.

        If the value is already an HTTP(S) URL (e.g. an Azure Blob Storage URL
        from a remixed/copied message), it is kept as-is since the file already
        exists in storage.
        """
        for piece in request.pieces:
            # Only persist *_path types (image_path, audio_path, video_path, binary_path).
            # Other non-text types (url, reasoning, function_call, tool_call, etc.)
            # are text-like and must not be base64-decoded.
            if not piece.data_type.endswith("_path"):
                continue

            # Already a remote URL (e.g. signed blob URL from a remix) — keep as-is
            if piece.original_value.startswith(("http://", "https://")):
                if piece.converted_value is None:
                    piece.converted_value = piece.original_value
                continue

            # Already a local media URL (e.g. /api/media?path=...) — extract the file path
            if piece.original_value.startswith("/api/media"):
                parsed = urlparse(piece.original_value)
                file_path = parse_qs(parsed.query).get("path", [None])[0]
                if file_path:
                    piece.original_value = file_path
                    if piece.converted_value is None:
                        piece.converted_value = file_path
                continue

            # Already an existing file on disk — keep as-is
            if Path(piece.original_value).is_file():
                if piece.converted_value is None:
                    piece.converted_value = piece.original_value
                continue

            # Derive file extension from the MIME type sent by the frontend
            ext = None
            if piece.mime_type:
                ext = mimetypes.guess_extension(piece.mime_type, strict=False)
            if not ext:
                ext = ".bin"

            # Strip data URI prefix if present (e.g. "data:image/png;base64,...")
            # The backend itself returns data URIs from pyrit_messages_to_dto_async,
            # so the client may echo them back.
            value = piece.original_value
            if value.startswith("data:"):
                # Format: data:<mime>;base64,<payload>
                _, _, payload = value.partition(",")
                value = payload

            serializer = data_serializer_factory(
                category="prompt-memory-entries",
                data_type=cast("PromptDataType", piece.data_type),
                extension=ext,
            )
            await serializer.save_b64_image(data=value)
            file_path = serializer.value
            piece.original_value = file_path
            if piece.converted_value is None:
                piece.converted_value = file_path

    async def _store_prepended_messages(
        self,
        conversation_id: str,
        prepended: list[Any],
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Store prepended conversation messages in memory."""
        for seq, msg in enumerate(prepended):
            for p in msg.pieces:
                piece = request_piece_to_pyrit_message_piece(
                    piece=p,
                    role=msg.role,
                    conversation_id=conversation_id,
                    sequence=seq,
                    labels=labels,
                )
                self._memory.add_message_pieces_to_memory(message_pieces=[piece])

    async def _send_and_store_message_async(
        self,
        *,
        conversation_id: str,
        target_registry_name: str,
        request: AddMessageRequest,
        sequence: int,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Send message to target via normalizer and store response."""
        target_obj = get_target_service().get_target_object(target_registry_name=target_registry_name)
        if not target_obj:
            raise ValueError(f"Target object for '{target_registry_name}' not found")

        await self._persist_base64_pieces_async(request)

        self._resolve_video_remix_metadata(request)

        pyrit_message = request_to_pyrit_message(
            request=request,
            conversation_id=conversation_id,
            sequence=sequence,
            labels=labels,
        )

        converter_configs = self._get_converter_configs(request)

        normalizer = PromptNormalizer()
        await normalizer.send_prompt_async(
            message=pyrit_message,
            target=target_obj,
            conversation_id=conversation_id,
            request_converter_configurations=converter_configs,
            labels=labels,
        )
        # PromptNormalizer stores both request and response in memory automatically

    async def _store_message_only_async(
        self,
        *,
        conversation_id: str,
        request: AddMessageRequest,
        sequence: int,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Store message without sending (send=False)."""
        await self._persist_base64_pieces_async(request)
        for p in request.pieces:
            piece = request_piece_to_pyrit_message_piece(
                piece=p,
                role=request.role,
                conversation_id=conversation_id,
                sequence=sequence,
                labels=labels,
            )
            self._memory.add_message_pieces_to_memory(message_pieces=[piece])

    def _resolve_video_remix_metadata(self, request: AddMessageRequest) -> None:
        """
        Auto-resolve video_id metadata for remix mode.

        When a video_path piece is carried over from a previous conversation
        (via original_prompt_id) alongside a text piece, the video target
        requires video_id in the text piece's prompt_metadata. This method
        looks up the original piece's metadata and propagates the video_id.
        """
        video_pieces = [p for p in request.pieces if p.data_type == "video_path"]
        if not video_pieces:
            return

        text_piece = next((p for p in request.pieces if p.data_type == "text"), None)
        if not text_piece:
            return

        # Already has video_id — nothing to resolve
        if text_piece.prompt_metadata and text_piece.prompt_metadata.get("video_id"):
            return

        # Try to resolve video_id from the original prompt piece
        for vp in video_pieces:
            if not vp.original_prompt_id:
                continue
            original_pieces = self._memory.get_message_pieces(prompt_ids=[vp.original_prompt_id])
            if not original_pieces:
                continue
            video_id = (original_pieces[0].prompt_metadata or {}).get("video_id")
            if video_id:
                if text_piece.prompt_metadata is None:
                    text_piece.prompt_metadata = {}
                text_piece.prompt_metadata["video_id"] = video_id
                # Also set video_id on the video piece itself
                if vp.prompt_metadata is None:
                    vp.prompt_metadata = {}
                vp.prompt_metadata["video_id"] = video_id
                return

    def _get_converter_configs(self, request: AddMessageRequest) -> list[PromptConverterConfiguration]:
        """
        Get converter configurations if needed.

        Returns:
            List of PromptConverterConfiguration for the converters.
        """
        has_preconverted = any(p.converted_value is not None for p in request.pieces)
        if has_preconverted or not request.converter_ids:
            return []

        converters = get_converter_service().get_converter_objects_for_ids(converter_ids=request.converter_ids)
        return PromptConverterConfiguration.from_converters(converters=converters)


# ============================================================================
# Singleton
# ============================================================================


@lru_cache(maxsize=1)
def get_attack_service() -> AttackService:
    """
    Get the global attack service instance.

    Returns:
        The singleton AttackService instance.
    """
    return AttackService()
