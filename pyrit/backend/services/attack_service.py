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

import base64
import binascii
import hashlib
import json
import logging
import mimetypes
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urlparse

from pyrit.backend.mappers import (
    attack_result_to_summary_async,
    format_last_message_preview,
    pyrit_messages_to_dto_async,
    request_piece_to_pyrit_message_piece,
    request_to_pyrit_message,
)
from pyrit.backend.models import DEFAULT_MEDIA_EXTENSIONS
from pyrit.backend.models.attacks import (
    AddMessageRequest,
    AddMessageResponse,
    AttackConversationsResponse,
    AttackListResponse,
    AttackSummary,
    ConversationMessagesResponse,
    ConversationSummary,
    ConverterConfigurationRequest,
    CreateAttackRequest,
    CreateAttackResponse,
    CreateConversationRequest,
    CreateConversationResponse,
    MessagePieceRequest,
    PrependedMessageRequest,
    UpdateAttackRequest,
    UpdateMainConversationRequest,
    UpdateMainConversationResponse,
)
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.services.converter_service import get_converter_service
from pyrit.backend.services.target_service import get_target_service
from pyrit.common.deprecation import print_deprecation_message
from pyrit.memory import AttackResultKeysetCursor, CentralMemory, data_serializer_factory
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    Conversation,
    ConversationStats,
    ConversationType,
    ConverterIdentifier,
    PromptDataType,
)
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer

logger = logging.getLogger(__name__)


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
        attack_types: Sequence[str] | None = None,
        converter_types: Sequence[str] | None = None,
        converter_types_match: Literal["any", "all"] = "all",
        has_converters: bool | None = None,
        outcome: Literal["undetermined", "success", "failure", "error"] | None = None,
        labels: dict[str, str | Sequence[str]] | None = None,
        min_turns: int | None = None,
        max_turns: int | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> AttackListResponse:
        """
        List attacks with optional filtering and pagination.

        Queries AttackResult entries from the database.

        Args:
            attack_types: Filter by attack type names (case-insensitive). May be specified
                multiple times to OR-match across types. None or empty list applies no filter.
            converter_types: Filter by converter class names (case-insensitive).
                ``None`` or an empty list applies no filter at this layer. Combination
                semantics for multiple entries are controlled by ``converter_types_match``.
                To restrict results to attacks with no converters, pass
                ``has_converters=False`` instead.
            converter_types_match: How to combine multiple entries in ``converter_types``.
                ``"all"`` (default) matches attacks that used every listed converter.
                ``"any"`` matches attacks that used at least one of the listed converters.
                Ignored when ``converter_types`` is None or has fewer than 2 entries.
            has_converters: Filter by converter presence. ``True`` returns only attacks that
                used at least one converter. ``False`` returns only attacks that used no
                converters. ``None`` applies no filter.
            outcome: Filter by attack outcome.
            labels: Filter by labels. See ``MemoryInterface.get_attack_results`` for
                semantics (AND across label names; string equality or sequence OR within
                each name).
            min_turns: Filter by minimum executed turns.
            max_turns: Filter by maximum executed turns.
            limit: Maximum items to return.
            cursor: Opaque pagination token from a previous response's ``next_cursor``.
                Omit (or pass ``None``) to fetch the first page.

        Returns:
            AttackListResponse with filtered and paginated attack summaries.
        """
        # Phase 1: Query + lightweight filtering (no pieces needed)
        # Coerce an empty converter_types list to None so it behaves as "no filter" at
        # this layer — the "attacks with no converters" case is expressed through
        # has_converters=False, which keeps the three layers (route/service/memory)
        # consistent.
        effective_converter_types = converter_types if converter_types else None

        # The cursor encodes both a keyset (seek) anchor — the recency sort key of the last
        # row on the previous page — and a fingerprint of the filters it was generated for.
        # Decoding against the current request's filters makes a cursor minted for a different
        # filter set fall back to the first page instead of seeking within the wrong result
        # set. The memory layer deduplicates, applies the turn bounds, orders by recency, seeks
        # past the anchor, and limits in SQL, so only one page's worth of rows is materialized
        # instead of the full table.
        filter_fingerprint = self._attack_filter_fingerprint(
            attack_types=attack_types,
            converter_types=effective_converter_types,
            converter_types_match=converter_types_match,
            has_converters=has_converters,
            outcome=outcome,
            labels=labels if labels else None,
            min_turns=min_turns,
            max_turns=max_turns,
        )
        after = self._decode_attack_cursor(cursor=cursor, fingerprint=filter_fingerprint)
        results = self._memory.get_attack_results(
            outcome=outcome,
            labels=labels if labels else None,
            attack_classes=attack_types if attack_types else None,
            converter_classes=effective_converter_types,
            converter_classes_match=converter_types_match,
            has_converters=has_converters,
            min_turns=min_turns,
            max_turns=max_turns,
            limit=limit + 1,
            after=after,
        )

        # Over-fetch by one row to detect whether a further page exists.
        has_next_page = len(results) > limit
        page_results = list(results[:limit])
        next_cursor = (
            self._encode_attack_cursor(
                cursor=AttackResultKeysetCursor.from_attack_result(page_results[-1]),
                fingerprint=filter_fingerprint,
            )
            if has_next_page and page_results
            else None
        )

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
            preview_data_type = main_stats.last_message_data_type if main_stats else None
            conv_labels = (main_stats.labels if main_stats else None) or {}

            merged = ConversationStats(
                message_count=total_count,
                last_message_preview=preview,
                last_message_data_type=preview_data_type,
                labels=conv_labels,
            )

            page.append(await attack_result_to_summary_async(ar, stats=merged))

        return AttackListResponse(
            items=page,
            pagination=PaginationInfo(limit=limit, has_more=has_next_page, next_cursor=next_cursor, prev_cursor=cursor),
        )

    async def get_attack_options_async(self) -> list[str]:
        """
        Get all unique attack type names from stored attack results.

        Delegates to the memory layer which extracts distinct class_name
        values from the atomic_attack_identifier JSON column via SQL.

        Returns:
            Sorted list of unique attack type names.
        """
        return self._memory.get_unique_attack_class_names()

    async def get_converter_options_async(self) -> list[str]:
        """
        Get all unique converter type names used across attack results.

        Delegates to the memory layer which extracts distinct converter
        type names from the atomic_attack_identifier JSON column via SQL.

        Returns:
            Sorted list of unique converter type names.
        """
        return self._memory.get_unique_converter_class_names()

    async def get_attack_async(self, *, attack_result_id: str) -> AttackSummary | None:
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
        return await attack_result_to_summary_async(ar, stats=stats)

    async def get_conversation_messages_async(
        self,
        *,
        attack_result_id: str,
        conversation_id: str,
    ) -> ConversationMessagesResponse | None:
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
        pyrit_messages = self._memory.get_conversation_messages(conversation_id=conversation_id)
        backend_messages = await pyrit_messages_to_dto_async(
            list(pyrit_messages),
            objective_score_id=ar.last_score.id if ar.last_score else None,
        )

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
        stores the new labels on the attack result, and maps assistant roles
        to ``simulated_assistant`` so the branched context is inert.

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
                remap_assistant_to_simulated=True,
                target_identifier=target_identifier,
            )
        else:
            conversation_id = str(uuid.uuid4())

        # Create AttackResult. An absent request.name persists as an empty
        # objective rather than a sentinel placeholder string -- both
        # AttackResult.objective and the database column are non-nullable,
        # but an empty string is a valid value the frontend already treats
        # as "no explicit objective".
        attack_result = AttackResult(
            conversation_id=conversation_id,
            objective=request.name or "",
            atomic_attack_identifier=AtomicAttackIdentifier.build(
                attack_identifier=AttackIdentifier(
                    class_name=request.name or "ManualAttack",
                    class_module="pyrit.backend",
                    objective_target=target_identifier,
                ),
            ),
            outcome=AttackOutcome.UNDETERMINED,
            timestamp=now,
            metadata={
                "created_at": now.isoformat(),
                "target_registry_name": request.target_registry_name,
            },
            labels=labels,
        )

        # Store in memory
        self._memory.add_attack_results_to_memory(attack_results=[attack_result])

        # Store prepended conversation messages if provided. A system_prompt is lowered to a
        # single system-role message at the front, composing with any prepended_conversation.
        prepended = list(request.prepended_conversation or [])
        if request.system_prompt:
            prepended.insert(
                0,
                PrependedMessageRequest(
                    role="system",
                    pieces=[MessagePieceRequest(original_value=request.system_prompt)],
                ),
            )
        if prepended:
            await self._store_prepended_messages_async(
                conversation_id=conversation_id,
                prepended=prepended,
                target_identifier=target_identifier,
            )

        return CreateAttackResponse(
            attack_result_id=attack_result.attack_result_id,
            conversation_id=conversation_id,
            created_at=now,
        )

    async def update_attack_async(self, *, attack_result_id: str, request: UpdateAttackRequest) -> AttackSummary | None:
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
            "error": AttackOutcome.ERROR,
        }
        new_outcome = outcome_map.get(request.outcome, AttackOutcome.UNDETERMINED)

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields={
                "outcome": new_outcome.value,
                "timestamp": datetime.now(timezone.utc),
            },
        )

        return await self.get_attack_async(attack_result_id=attack_result_id)

    async def get_conversations_async(self, *, attack_result_id: str) -> AttackConversationsResponse | None:
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
            # SQLite returns naive datetimes — normalize to UTC (same pattern as the UTCDateTime column type)
            if created_at is not None and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            conversations.append(
                ConversationSummary(
                    conversation_id=conv_id,
                    message_count=stats.message_count if stats else 0,
                    last_message_preview=format_last_message_preview(
                        value=stats.last_message_preview if stats else None,
                        data_type=stats.last_message_data_type if stats else None,
                    ),
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
    ) -> CreateConversationResponse | None:
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
            source_metadata = self._memory._get_conversation(conversation_id=request.source_conversation_id)
            new_conversation_id = self._duplicate_conversation_up_to(
                source_conversation_id=request.source_conversation_id,
                cutoff_index=request.cutoff_index,
                target_identifier=source_metadata.target_identifier if source_metadata else None,
            )
        else:
            new_conversation_id = str(uuid.uuid4())

        # Add to pruned_conversation_ids so user-created branches are visible in the GUI history panel.
        existing_pruned = ar.get_pruned_conversation_ids()

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields={
                "pruned_conversation_ids": existing_pruned + [new_conversation_id],
                "timestamp": now,
            },
        )

        return CreateConversationResponse(conversation_id=new_conversation_id, created_at=now)

    async def update_main_conversation_async(
        self, *, attack_result_id: str, request: UpdateMainConversationRequest
    ) -> UpdateMainConversationResponse | None:
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

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields={
                "conversation_id": target_conv_id,
                "pruned_conversation_ids": updated_pruned if updated_pruned else None,
                "adversarial_chat_conversation_ids": updated_adversarial if updated_adversarial else None,
                "timestamp": now,
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

        self._validate_target_match(attack_identifier=ar.get_attack_strategy_identifier(), request=request)
        self._validate_operator_match(attack_result=ar, request=request)

        msg_conversation_id = request.target_conversation_id

        # Validate the target conversation belongs to this attack (main + pruned only)
        if msg_conversation_id not in ar.get_active_conversation_ids():
            raise ValueError(f"Conversation '{msg_conversation_id}' is not part of attack '{attack_result_id}'")

        target_registry_name = request.target_registry_name
        if request.send and not target_registry_name:
            raise ValueError("target_registry_name is required when send=True")

        request_converter_configs = self._resolve_request_converter_configs(request=request)
        response_converter_configs = self._resolve_converter_configs(
            configurations=request.response_converter_configurations
        )
        preconverted_indexes = {
            index for index, piece in enumerate(request.pieces) if piece.converted_value is not None
        }

        # Get existing messages to determine sequence.
        # NOTE: This read-then-write is not atomic (TOCTOU). Fine for the
        # current single-user UI, but would need a DB-level sequence
        # generator or optimistic locking if concurrent writes are supported.
        existing = self._memory.get_message_pieces(conversation_id=msg_conversation_id)
        sequence = max((p.sequence for p in existing), default=-1) + 1

        if request.send:
            assert target_registry_name is not None  # validated above
            try:
                await self._send_and_store_message_async(
                    conversation_id=msg_conversation_id,
                    target_registry_name=target_registry_name,
                    request=request,
                    sequence=sequence,
                    request_converter_configurations=request_converter_configs,
                    response_converter_configurations=response_converter_configs,
                    preconverted_indexes=preconverted_indexes,
                )
            except Exception:
                # PromptNormalizer persists a full error piece (response_error +
                # traceback) to memory *before* re-raising. Surface that stored
                # piece inline so the send (POST) response matches the
                # conversation-reload (GET) view instead of collapsing to a
                # generic 500. If no new error piece was stored (the failure
                # happened before the send, e.g. target lookup), re-raise so the
                # route still reports a real error.
                prior_ids = {p.id for p in existing}
                current_pieces = self._memory.get_message_pieces(conversation_id=msg_conversation_id)
                if not any(p.id not in prior_ids and p.has_error() for p in current_pieces):
                    raise
                logger.exception(
                    "Send failed for attack '%s' conversation '%s'; surfacing stored error piece.",
                    attack_result_id,
                    msg_conversation_id,
                )
        else:
            existing_metadata = self._memory._get_conversation(conversation_id=msg_conversation_id)
            await self._store_message_only_async(
                conversation_id=msg_conversation_id,
                request=request,
                sequence=sequence,
                target_identifier=existing_metadata.target_identifier if existing_metadata else None,
            )

        await self._update_attack_after_message_async(
            attack_result_id=attack_result_id,
            ar=ar,
            request_converter_configurations=request_converter_configs,
            response_converter_configurations=response_converter_configs,
        )

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
        self, *, attack_identifier: ComponentIdentifier | None, request: AddMessageRequest
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
        if stored_target_id.hash != request_target_id.hash:
            raise ValueError(
                f"Target mismatch: attack was created with {stored_target_id.unique_name} "
                f"but request uses {request_target_id.unique_name}. "
                f"Create a new attack to use a different target."
            )

    def _validate_operator_match(self, *, attack_result: AttackResult, request: AddMessageRequest) -> None:
        """
        Validate that the request operator matches the attack result's operator.

        Raises:
            ValueError: If the operator in the request doesn't match the attack result.
        """
        if not request.labels:
            return

        attack_operator = attack_result.labels.get("operator")
        if not attack_operator:
            return

        request_operator = request.labels.get("operator")
        if request_operator and request_operator != attack_operator:
            raise ValueError(
                f"Operator mismatch: attack belongs to operator '{attack_operator}' "
                f"but request is from '{request_operator}'. "
                f"Create a new attack to continue."
            )

    async def _update_attack_after_message_async(
        self,
        *,
        attack_result_id: str,
        ar: AttackResult,
        request_converter_configurations: list[ConverterConfiguration],
        response_converter_configurations: list[ConverterConfiguration],
    ) -> None:
        """
        Update attack recency and converter tracking after a message is added.

        Bumps the attack's ``timestamp`` column (the single indexed recency key) so the edited
        conversation re-floats to the top of the History view.

        Args:
            attack_result_id: The attack result to update.
            ar: The current attack result.
            request_converter_configurations: Resolved request converter configurations used for this message.
            response_converter_configurations: Resolved response converter configurations used for this message.
        """
        update_fields: dict[str, Any] = {"timestamp": datetime.now(timezone.utc)}

        request_converter_ids = self._get_converter_identifiers(configurations=request_converter_configurations)
        response_converter_ids = self._get_converter_identifiers(configurations=response_converter_configurations)
        if request_converter_ids or response_converter_ids:
            attack_strategy_identifier = ar.get_attack_strategy_identifier()
            if attack_strategy_identifier and ar.atomic_attack_identifier:
                attack_id = AttackIdentifier.from_component_identifier(attack_strategy_identifier)
                merged_request_converters = self._merge_attack_result_converter_identifiers(
                    existing=attack_id.request_converters,
                    additions=request_converter_ids,
                )
                merged_response_converters = self._merge_attack_result_converter_identifiers(
                    existing=attack_id.response_converters,
                    additions=response_converter_ids,
                )
                if (
                    merged_request_converters != attack_id.request_converters
                    or merged_response_converters != attack_id.response_converters
                ):
                    new_attack_id = self._replace_converter_pipelines(
                        attack_id,
                        request_converters=merged_request_converters,
                        response_converters=merged_response_converters,
                    )
                    new_atomic = self._replace_attack_in_atomic(
                        AtomicAttackIdentifier.from_component_identifier(ar.atomic_attack_identifier),
                        attack=new_attack_id,
                    )
                    update_fields["atomic_attack_identifier"] = new_atomic.model_dump()

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields=update_fields,
        )

    @staticmethod
    def _replace_converter_pipelines(
        attack_id: AttackIdentifier,
        *,
        request_converters: list[ConverterIdentifier],
        response_converters: list[ConverterIdentifier],
    ) -> AttackIdentifier:
        """
        Return a copy of ``attack_id`` with its converter pipelines replaced.

        Reconstructed through the constructor (not ``model_copy``) so the
        after-validator re-mirrors the typed converters into ``children`` and
        recomputes the content hash. All other params/children/attributes are
        preserved, so the identifier hashes identically apart from the converters.

        Returns:
            AttackIdentifier: A new identifier with the given converter pipelines.
        """
        return AttackIdentifier(
            class_name=attack_id.class_name,
            class_module=attack_id.class_module,
            params=dict(attack_id.params),
            children=dict(attack_id.children),
            attributes=dict(attack_id.attributes),
            request_converters=request_converters,
            response_converters=response_converters,
        )

    @staticmethod
    def _merge_attack_result_converter_identifiers(
        *,
        existing: list[ConverterIdentifier],
        additions: list[ConverterIdentifier],
    ) -> list[ConverterIdentifier]:
        """
        Merge converter usage into the aggregate attack result metadata.

        Attack result converter lists record which converters the attack used, not
        the exact converter pipeline for each message. Keep the first occurrence of
        each identifier across messages while preserving first-use order.

        Args:
            existing: Converter identifiers already recorded on the attack result.
            additions: Converter identifiers used by the new message.

        Returns:
            list[ConverterIdentifier]: Aggregate converter identifiers in first-use order.
        """
        merged = list(existing)
        existing_hashes = {converter.hash for converter in existing}
        for converter in additions:
            if converter.hash not in existing_hashes:
                merged.append(converter)
                existing_hashes.add(converter.hash)
        return merged

    @staticmethod
    def _replace_attack_in_atomic(
        atomic: AtomicAttackIdentifier, *, attack: AttackIdentifier
    ) -> AtomicAttackIdentifier:
        """
        Return a copy of ``atomic`` with its nested attack strategy replaced.

        Handles both the current nested shape (``atomic -> attack_technique ->
        attack``) and the legacy flat shape (``atomic -> attack``). Everything
        else is preserved so the composite identifier hashes identically apart
        from the swapped attack node.

        Returns:
            AtomicAttackIdentifier: A new composite identifier wrapping ``attack``.
        """
        technique = atomic.attack_technique
        if technique is not None:
            new_technique = AttackTechniqueIdentifier(
                class_name=technique.class_name,
                class_module=technique.class_module,
                params=dict(technique.params),
                children=dict(technique.children),
                attributes=dict(technique.attributes),
                attack=attack,
            )
            return AtomicAttackIdentifier(
                class_name=atomic.class_name,
                class_module=atomic.class_module,
                params=dict(atomic.params),
                children=dict(atomic.children),
                attributes=dict(atomic.attributes),
                attack_technique=new_technique,
            )
        # Legacy flat shape: the attack strategy lives in children["attack"].
        atomic_children = dict(atomic.children)
        atomic_children["attack"] = attack
        return AtomicAttackIdentifier(
            class_name=atomic.class_name,
            class_module=atomic.class_module,
            params=dict(atomic.params),
            children=atomic_children,
            attributes=dict(atomic.attributes),
        )

    # ========================================================================
    # Private Helper Methods - Pagination
    # ========================================================================

    @staticmethod
    def _attack_filter_fingerprint(
        *,
        attack_types: Sequence[str] | None = None,
        converter_types: Sequence[str] | None = None,
        converter_types_match: str = "all",
        has_converters: bool | None = None,
        outcome: str | None = None,
        labels: dict[str, str | Sequence[str]] | None = None,
        min_turns: int | None = None,
        max_turns: int | None = None,
    ) -> str:
        """
        Compute a stable, opaque fingerprint of the filters that define a result set.

        A pagination cursor is only meaningful for the exact filter set it was generated
        against. Embedding this fingerprint in the cursor lets ``_decode_attack_cursor``
        detect a cursor minted for a different filter set and fall back to the first page,
        instead of seeking with a keyset anchor that belongs to a different result set.
        Sequence and label filters are order-normalized so a semantically identical filter
        set always fingerprints the same regardless of argument order.

        Returns:
            A short hex digest that is stable for a given set of filter values.
        """

        def _norm_seq(values: Sequence[str] | None) -> list[str] | None:
            return sorted(str(v) for v in values) if values else None

        def _norm_labels(
            raw: dict[str, str | Sequence[str]] | None,
        ) -> dict[str, str | list[str]] | None:
            if not raw:
                return None
            normalized: dict[str, str | list[str]] = {}
            for key in sorted(raw):
                value = raw[key]
                if isinstance(value, str):
                    normalized[key] = value
                    continue
                # Drop empty sequences: get_attack_results treats an empty-sequence label as
                # "no filter" (see effective_labels), so including it here would fingerprint
                # a request differently from the equivalent no-op filter and spuriously reset
                # pagination to the first page.
                candidates = sorted(str(v) for v in value)
                if not candidates:
                    continue
                normalized[key] = candidates
            return normalized or None

        payload = {
            "attack_types": _norm_seq(attack_types),
            "converter_types": _norm_seq(converter_types),
            "converter_types_match": converter_types_match,
            "has_converters": has_converters,
            "outcome": outcome,
            "labels": _norm_labels(labels),
            "min_turns": min_turns,
            "max_turns": max_turns,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _encode_attack_cursor(*, cursor: AttackResultKeysetCursor, fingerprint: str) -> str:
        """
        Encode a keyset anchor and its filter fingerprint into an opaque pagination cursor.

        The anchor's timestamp can contain ``.``/``:``/``-`` (ISO timestamps), so the payload
        is JSON-serialized and base64url-encoded rather than joined with a delimiter, keeping
        the cursor an unambiguous opaque token for ``_decode_attack_cursor``.

        Returns:
            An opaque base64url cursor string encoding ``{fingerprint, timestamp,
            attack_result_id}``.
        """
        payload = {
            "f": fingerprint,
            "t": cursor.timestamp.isoformat(),
            "i": cursor.attack_result_id,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_attack_cursor(*, cursor: str | None, fingerprint: str) -> AttackResultKeysetCursor | None:
        """
        Decode the opaque list-attacks cursor into a keyset (seek) anchor.

        The cursor encodes the previous page's last-row timestamp anchor together with a
        fingerprint of the filter set it was generated for (see ``_attack_filter_fingerprint``).
        A cursor is honored only when its fingerprint matches the current request's filters;
        malformed, legacy (offset/attack-result-id/recency-string), or filter-mismatched cursors
        fall back to the first page (``None``) so a stale cursor degrades gracefully instead of
        raising or seeking within the wrong result set.

        Returns:
            The decoded ``AttackResultKeysetCursor``, or ``None`` to start at the first page.
        """
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        except (binascii.Error, ValueError, TypeError):
            return None
        if not isinstance(payload, dict) or payload.get("f") != fingerprint:
            return None
        raw_timestamp = payload.get("t")
        attack_result_id = payload.get("i")
        if not isinstance(raw_timestamp, str) or not isinstance(attack_result_id, str):
            return None
        try:
            timestamp = datetime.fromisoformat(raw_timestamp)
            uuid.UUID(attack_result_id)
        except ValueError:
            return None
        if timestamp.tzinfo is None:
            # Service-minted cursors always carry an aware (UTC) timestamp (AttackResult.timestamp
            # is timezone-aware). A naive timestamp means a crafted or corrupted cursor whose anchor
            # would bind inconsistently against the aware timestamp column, so restart at page one.
            return None
        # Canonicalize to UTC so the tie-break comparison matches the UTC-normalized timestamp
        # column regardless of the offset a crafted cursor encodes (service cursors are already UTC).
        try:
            timestamp = timestamp.astimezone(timezone.utc)
        except (OverflowError, OSError):
            # A crafted cursor near datetime's min/max with a large UTC offset overflows the
            # representable range when shifted to UTC; treat it as malformed and restart at page one.
            return None
        return AttackResultKeysetCursor(timestamp=timestamp, attack_result_id=attack_result_id)

    # ========================================================================
    # Private Helper Methods - Duplicate / Branch
    # ========================================================================

    def _duplicate_conversation_up_to(
        self,
        *,
        source_conversation_id: str,
        cutoff_index: int,
        remap_assistant_to_simulated: bool = False,
        target_identifier: ComponentIdentifier | None = None,
    ) -> str:
        """
        Duplicate messages from a conversation up to and including a turn index.

        Uses the memory layer's ``duplicate_messages`` so that each new
        piece gets a fresh ``id`` and ``timestamp`` while preserving
        ``original_prompt_id`` for tracking lineage.

        Args:
            source_conversation_id: The conversation to copy from.
            cutoff_index: Include messages with sequence <= cutoff_index.
            remap_assistant_to_simulated: When True, pieces with role
                ``assistant`` are changed to ``simulated_assistant`` so the
                branched context is inert and won't confuse the target.

            target_identifier (ComponentIdentifier | None): The target the new conversation
                is held with, if known. Recorded once for the duplicated conversation.

        Returns:
            The new conversation ID containing the duplicated messages.
        """
        messages = self._memory.get_conversation_messages(conversation_id=source_conversation_id)
        messages_to_copy = [m for m in messages if m.sequence <= cutoff_index]

        new_conversation_id, all_pieces = self._memory.duplicate_messages(messages=messages_to_copy)

        # Apply optional overrides to the fresh pieces before persisting
        for piece in all_pieces:
            if remap_assistant_to_simulated and piece.api_role == "assistant":
                piece.role = "simulated_assistant"

        if all_pieces:
            self._memory.add_conversation_to_memory(
                conversation=Conversation(conversation_id=new_conversation_id, target_identifier=target_identifier)
            )
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

            # Already an existing file on disk — keep as-is.
            try:
                if Path(piece.original_value).is_file():
                    if piece.converted_value is None:
                        piece.converted_value = piece.original_value
                    continue
            except (OSError, ValueError):
                pass

            # Strip data URI prefix if present (e.g. "data:image/png;base64,...")
            # The backend itself returns data URIs from pyrit_messages_to_dto_async,
            # so the client may echo them back.
            value = piece.original_value
            data_uri_mime_type = None
            if value.startswith("data:"):
                # Format: data:<mime>;base64,<payload>
                header, _, payload = value.partition(",")
                data_uri_mime_type = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else None
                value = payload

            # Derive file extension from MIME metadata, then fall back to data_type.
            ext = None
            if piece.mime_type:
                ext = mimetypes.guess_extension(piece.mime_type, strict=False)
            if not ext and data_uri_mime_type:
                ext = mimetypes.guess_extension(data_uri_mime_type, strict=False)
            if not ext:
                ext = DEFAULT_MEDIA_EXTENSIONS.get(piece.data_type, ".bin")

            serializer = data_serializer_factory(
                category="prompt-memory-entries",
                data_type=cast("PromptDataType", piece.data_type),
                extension=ext,
            )
            await serializer.save_b64_image_async(data=value)
            file_path = serializer.value
            piece.original_value = file_path
            if piece.converted_value is None:
                piece.converted_value = file_path

    async def _store_prepended_messages_async(
        self,
        *,
        conversation_id: str,
        prepended: list[Any],
        target_identifier: ComponentIdentifier | None = None,
    ) -> None:
        """Store prepended conversation messages in memory."""
        if not prepended:
            return
        self._memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier)
        )
        for seq, msg in enumerate(prepended):
            for p in msg.pieces:
                piece = request_piece_to_pyrit_message_piece(
                    piece=p,
                    role=msg.role,
                    conversation_id=conversation_id,
                    sequence=seq,
                )
                self._memory.add_message_pieces_to_memory(message_pieces=[piece])

    async def _send_and_store_message_async(
        self,
        *,
        conversation_id: str,
        target_registry_name: str,
        request: AddMessageRequest,
        sequence: int,
        request_converter_configurations: list[ConverterConfiguration],
        response_converter_configurations: list[ConverterConfiguration],
        preconverted_indexes: set[int],
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
        )

        request_converter_configurations = self._exclude_preconverted_piece_indexes(
            configurations=request_converter_configurations,
            preconverted_indexes=preconverted_indexes,
            piece_count=len(request.pieces),
        )

        normalizer = PromptNormalizer()
        await normalizer.send_prompt_async(
            message=pyrit_message,
            target=target_obj,
            conversation_id=conversation_id,
            request_converter_configurations=request_converter_configurations,
            response_converter_configurations=response_converter_configurations,
        )
        # PromptNormalizer stores both request and response in memory automatically

    async def _store_message_only_async(
        self,
        *,
        conversation_id: str,
        request: AddMessageRequest,
        sequence: int,
        target_identifier: ComponentIdentifier | None = None,
    ) -> None:
        """Store message without sending (send=False)."""
        await self._persist_base64_pieces_async(request)
        self._memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier)
        )
        for p in request.pieces:
            piece = request_piece_to_pyrit_message_piece(
                piece=p,
                role=request.role,
                conversation_id=conversation_id,
                sequence=sequence,
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

    def _resolve_request_converter_configs(self, *, request: AddMessageRequest) -> list[ConverterConfiguration]:
        """
        Resolve legacy or structured request converter configurations.

        Returns:
            list[ConverterConfiguration]: Resolved request configurations.
        """
        if request.converter_ids is not None:
            print_deprecation_message(
                old_item="AddMessageRequest.converter_ids",
                new_item="AddMessageRequest.request_converter_configurations",
                removed_in="1.3.0",
            )
        if request.converter_ids:
            converters = get_converter_service().get_converter_objects_for_ids(converter_ids=request.converter_ids)
            return ConverterConfiguration.from_converters(converters=converters)

        return self._resolve_converter_configs(configurations=request.request_converter_configurations)

    def _resolve_converter_configs(
        self,
        *,
        configurations: list[ConverterConfigurationRequest] | None,
    ) -> list[ConverterConfiguration]:
        """
        Resolve registry-backed converter configurations.

        Returns:
            list[ConverterConfiguration]: Resolved configurations in request order.
        """
        converter_service = get_converter_service()
        return [
            ConverterConfiguration(
                converters=converter_service.get_converter_objects_for_ids(converter_ids=configuration.converter_ids),
                indexes_to_apply=configuration.indexes_to_apply,
                prompt_data_types_to_apply=configuration.prompt_data_types_to_apply,
            )
            for configuration in configurations or []
        ]

    @staticmethod
    def _exclude_preconverted_piece_indexes(
        *,
        configurations: list[ConverterConfiguration],
        preconverted_indexes: set[int],
        piece_count: int,
    ) -> list[ConverterConfiguration]:
        """
        Exclude client-preconverted pieces from request converter configurations.

        Returns:
            list[ConverterConfiguration]: Configurations that still apply to at least one piece.
        """
        if not preconverted_indexes:
            return configurations

        filtered_configurations: list[ConverterConfiguration] = []
        for configuration in configurations:
            configured_indexes = configuration.indexes_to_apply
            candidate_indexes = range(piece_count) if configured_indexes is None else configured_indexes
            eligible_indexes = [index for index in candidate_indexes if index not in preconverted_indexes]
            if not eligible_indexes:
                continue
            filtered_configurations.append(
                ConverterConfiguration(
                    converters=configuration.converters,
                    indexes_to_apply=eligible_indexes,
                    prompt_data_types_to_apply=configuration.prompt_data_types_to_apply,
                )
            )
        return filtered_configurations

    @staticmethod
    def _get_converter_identifiers(*, configurations: list[ConverterConfiguration]) -> list[ConverterIdentifier]:
        """
        Flatten resolved converter identifiers in configuration order.

        Returns:
            list[ConverterIdentifier]: The converter identifiers.
        """
        return [
            ConverterIdentifier.from_component_identifier(converter.get_identifier())
            for configuration in configurations
            for converter in configuration.converters
        ]


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
