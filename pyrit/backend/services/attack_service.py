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
- Human-led attacks may branch into multiple conversations under the same AttackResult
- AI-generated attacks may have multiple related conversations
"""

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal

from pyrit.backend.mappers import (
    attack_result_to_summary_async,
    format_last_message_preview,
    pyrit_messages_to_dto_async,
    request_piece_to_pyrit_message_piece,
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
    MessagePieceRequest,
    MessageView,
    PrependedMessageRequest,
    TargetResponseStatus,
    UpdateAttackRequest,
    UpdateMainConversationRequest,
    UpdateMainConversationResponse,
)
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.services.message_send_service import MessageSendService, resolve_applied_converter_identifiers
from pyrit.backend.services.pagination import (
    decode_keyset_cursor,
    encode_keyset_cursor,
    fingerprint_filters,
    normalize_label_filters,
)
from pyrit.backend.services.target_service import get_target_service
from pyrit.common.utils import to_sha256
from pyrit.memory import AttackResultKeysetCursor, CentralMemory
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Conversation,
    ConversationStats,
    MessagePiece,
)


def _get_latest_target_response_status(messages: list[MessageView]) -> TargetResponseStatus | None:
    """Return error metadata when the conversation ends with a real target response."""
    latest_response = messages[-1] if messages else None
    if not latest_response or latest_response.role != "assistant":
        return None

    request = next((message for message in reversed(messages[:-1]) if message.role == "user"), None)
    if not request:
        return None

    response_error = next(
        (piece.response_error for piece in latest_response.message_pieces if piece.response_error != "none"),
        "none",
    )
    return TargetResponseStatus(
        response_error=response_error,
        request_turn_number=request.turn_number,
        response_turn_number=latest_response.turn_number,
    )


class AttackObjectiveConflictError(Exception):
    """The attack already has a different objective."""


class AttackService:
    """
    Service for managing attacks.

    Uses PyRIT memory (database) as the source of truth via AttackResult.
    """

    def __init__(self) -> None:
        """Initialize the attack service."""
        self._memory = CentralMemory.get_memory_instance()
        self._message_send_service = MessageSendService(memory=self._memory)

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
        include_scenario_attacks: bool = True,
        outcome: Literal["undetermined", "success", "failure", "error"] | None = None,
        labels: Mapping[str, str | Sequence[str]] | None = None,
        operator: Sequence[str] | None = None,
        operation: Sequence[str] | None = None,
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
            include_scenario_attacks: Whether to include attacks created as part of scenario
                runs. Defaults to ``True`` for API compatibility.
            outcome: Filter by attack outcome.
            operator: Filter by dedicated operator values.
            operation: Filter by dedicated operation values.
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
        effective_attack_types = attack_types if attack_types else None

        # The cursor encodes both a keyset (seek) anchor — the recency sort key of the last
        # row on the previous page — and a fingerprint of the filters it was generated for.
        # Decoding against the current request's filters makes a cursor minted for a different
        # filter set fall back to the first page instead of seeking within the wrong result
        # set. The memory layer deduplicates, applies the turn bounds, orders by recency, seeks
        # past the anchor, and limits in SQL, so only one page's worth of rows is materialized
        # instead of the full table.
        normalized_labels = normalize_label_filters(labels=labels)
        fingerprint_values: dict[str, Any] = {
            "attack_types": effective_attack_types,
            "converter_types": effective_converter_types,
            "converter_types_match": converter_types_match,
            "has_converters": has_converters,
            "include_scenario_attacks": include_scenario_attacks,
            "outcome": outcome,
            "labels": normalized_labels,
            "min_turns": min_turns,
            "max_turns": max_turns,
        }
        if operator is not None:
            fingerprint_values["operator"] = operator
        if operation is not None:
            fingerprint_values["operation"] = operation
        filter_fingerprint = fingerprint_filters(filters=fingerprint_values)
        decoded_cursor = decode_keyset_cursor(cursor=cursor, fingerprint=filter_fingerprint)
        after = (
            AttackResultKeysetCursor(
                timestamp=decoded_cursor.timestamp,
                attack_result_id=decoded_cursor.identifier,
            )
            if decoded_cursor is not None
            else None
        )
        results = self._memory.get_attack_results(
            outcome=outcome,
            operator=operator,
            operation=operation,
            labels=normalized_labels,
            attack_classes=effective_attack_types,
            converter_classes=effective_converter_types,
            converter_classes_match=converter_types_match,
            has_converters=has_converters,
            include_scenario_attacks=include_scenario_attacks,
            min_turns=min_turns,
            max_turns=max_turns,
            limit=limit + 1,
            after=after,
        )

        # Over-fetch by one row to detect whether a further page exists.
        has_next_page = len(results) > limit
        page_results = list(results[:limit])
        next_cursor = (
            encode_keyset_cursor(
                timestamp=page_results[-1].timestamp,
                identifier=page_results[-1].attack_result_id,
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
        results = await asyncio.to_thread(
            self._memory.get_attack_results,
            attack_result_ids=[attack_result_id],
        )
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
            target_response_status=_get_latest_target_response_status(backend_messages),
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

        now = datetime.now(UTC)

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
            operator=request.operator,
            operation=request.operation,
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
        Update an attack's mutable fields.

        Updates the AttackResult in the database.

        Returns:
            Updated AttackSummary if found, None otherwise.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            return None

        update_fields: dict[str, Any] = {"timestamp": datetime.now(UTC)}
        if request.outcome is not None:
            outcome_map = {
                "undetermined": AttackOutcome.UNDETERMINED,
                "success": AttackOutcome.SUCCESS,
                "failure": AttackOutcome.FAILURE,
                "error": AttackOutcome.ERROR,
            }
            update_fields["outcome"] = outcome_map[request.outcome].value
        if request.objective is not None:
            existing_objective = results[0].objective
            if existing_objective and existing_objective != request.objective:
                raise AttackObjectiveConflictError(f"Attack '{attack_result_id}' already has an objective")
            if not existing_objective:
                update_fields["objective"] = request.objective
                update_fields["objective_sha256"] = to_sha256(request.objective)
            elif request.outcome is None:
                return await self.get_attack_async(attack_result_id=attack_result_id)

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields=update_fields,
        )

        return await self.get_attack_async(attack_result_id=attack_result_id)

    async def remove_human_score_async(self, *, attack_result_id: str) -> AttackSummary | None:
        """
        Remove the human-score override from an attack.

        The immutable score remains in memory. The attack outcome falls back to
        its automated true/false score, or to undetermined when none exists.

        Returns:
            Updated AttackSummary if found, None otherwise.
        """
        results = await asyncio.to_thread(
            self._memory.get_attack_results,
            attack_result_ids=[attack_result_id],
        )
        if not results:
            return None

        automated_score = results[0].automated_score
        if automated_score is None or automated_score.score_value is None:
            outcome = AttackOutcome.UNDETERMINED
            outcome_reason = None
        else:
            outcome = (
                AttackOutcome.SUCCESS if automated_score.score_value.casefold() == "true" else AttackOutcome.FAILURE
            )
            outcome_reason = automated_score.score_rationale

        await asyncio.to_thread(
            self._memory.update_attack_result_by_id,
            attack_result_id=attack_result_id,
            update_fields={
                "human_score_id": None,
                "outcome": outcome.value,
                "outcome_reason": outcome_reason,
                "timestamp": datetime.now(UTC),
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
                created_at = created_at.replace(tzinfo=UTC)
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
        now = datetime.now(UTC)
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
        results = await asyncio.to_thread(self._memory.get_attack_results, attack_result_ids=[attack_result_id])
        if not results:
            return None

        ar = results[0]
        now = datetime.now(UTC)

        # Validate that both or neither branching fields are provided
        if (request.source_conversation_id is None) != (request.cutoff_index is None):
            raise ValueError("Both source_conversation_id and cutoff_index must be provided together")

        # Validate source_conversation_id belongs to this attack
        if (
            request.source_conversation_id is not None
            and request.source_conversation_id not in ar.get_active_conversation_ids()
        ):
            raise ValueError(
                f"Conversation '{request.source_conversation_id}' is not part of attack '{attack_result_id}'"
            )

        attack_identifier = ar.get_attack_strategy_identifier()
        objective_target = attack_identifier.get_child("objective_target") if attack_identifier else None
        source_metadata: Conversation | None = None
        all_pieces: Sequence[MessagePiece] = []
        if request.source_conversation_id is not None and request.cutoff_index is not None:
            source_metadata = await asyncio.to_thread(
                self._memory._get_conversation, conversation_id=request.source_conversation_id
            )
            source_metadata = source_metadata or Conversation(
                conversation_id=request.source_conversation_id, target_identifier=objective_target
            )
            conversation, all_pieces = await asyncio.to_thread(
                self._prepare_conversation_up_to,
                source_conversation_id=request.source_conversation_id,
                cutoff_index=request.cutoff_index,
                target_identifier=source_metadata.target_identifier,
            )
        else:
            conversation = Conversation(
                conversation_id=str(uuid.uuid4()),
                target_identifier=objective_target,
            )

        stored = await asyncio.to_thread(
            self._memory.add_conversation_branches_to_attack,
            attack_result_id=attack_result_id,
            conversations=[conversation],
            message_pieces=all_pieces,
            source_conversation=source_metadata,
        )
        if not stored:
            return None

        return CreateConversationResponse(conversation_id=conversation.conversation_id, created_at=now)

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
        results = await asyncio.to_thread(self._memory.get_attack_results, attack_result_ids=[attack_result_id])
        if not results:
            return None

        ar = results[0]
        target_conv_id = request.conversation_id

        # Only user-visible conversations can become the main conversation.
        if target_conv_id not in ar.get_active_conversation_ids():
            raise ValueError(f"Conversation '{target_conv_id}' is not part of this attack")

        now = datetime.now(UTC)
        stored = await asyncio.to_thread(
            self._memory.promote_attack_conversation,
            attack_result_id=attack_result_id,
            conversation_id=target_conv_id,
        )
        if not stored:
            return None

        return UpdateMainConversationResponse(
            attack_result_id=attack_result_id,
            conversation_id=target_conv_id,
            updated_at=now,
        )

    async def add_message_async(self, *, attack_result_id: str, request: AddMessageRequest) -> AddMessageResponse:
        """
        Add a message and return the existing synchronous attack and conversation views.

        Returns:
            AddMessageResponse: Updated attack and messages after sending or storing.
        """
        await self._message_send_service.add_message_async(attack_result_id=attack_result_id, request=request)

        attack_detail = await self.get_attack_async(attack_result_id=attack_result_id)
        if attack_detail is None:
            raise ValueError(f"Attack '{attack_result_id}' not found after update")

        attack_messages = await self.get_conversation_messages_async(
            attack_result_id=attack_result_id,
            conversation_id=request.target_conversation_id,
        )
        if attack_messages is None:
            raise ValueError(f"Attack '{attack_result_id}' messages not found after update")

        return AddMessageResponse(attack=attack_detail, messages=attack_messages)

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
        conversation, all_pieces = self._prepare_conversation_up_to(
            source_conversation_id=source_conversation_id,
            cutoff_index=cutoff_index,
            target_identifier=target_identifier,
        )

        # Apply optional overrides to the fresh pieces before persisting
        for piece in all_pieces:
            if remap_assistant_to_simulated and piece.api_role == "assistant":
                piece.role = "simulated_assistant"

        if all_pieces:
            self._memory.add_conversation_to_memory(conversation=conversation)
            self._memory.add_message_pieces_to_memory(message_pieces=list(all_pieces))

        return conversation.conversation_id

    def _prepare_conversation_up_to(
        self,
        *,
        source_conversation_id: str,
        cutoff_index: int,
        target_identifier: ComponentIdentifier | None = None,
    ) -> tuple[Conversation, Sequence[MessagePiece]]:
        """
        Prepare a history copy without writing any rows.

        Returns:
            tuple[Conversation, Sequence[MessagePiece]]: New metadata and lineage-preserving pieces.
        """
        messages = self._memory.get_conversation_messages(conversation_id=source_conversation_id)
        new_id, pieces = self._memory.duplicate_messages(
            messages=[message for message in messages if message.sequence <= cutoff_index]
        )
        return Conversation(conversation_id=new_id, target_identifier=target_identifier), pieces

    # ========================================================================
    # Private Helper Methods - Store Messages
    # ========================================================================

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
        applied_by_message = [resolve_applied_converter_identifiers(msg.pieces) for msg in prepended]
        self._memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier)
        )
        for seq, msg in enumerate(prepended):
            for index, p in enumerate(msg.pieces):
                piece = request_piece_to_pyrit_message_piece(
                    piece=p,
                    role=msg.role,
                    conversation_id=conversation_id,
                    sequence=seq,
                )
                piece.converter_identifiers.extend(applied_by_message[seq].get(index, []))
                self._memory.add_message_pieces_to_memory(message_pieces=[piece])


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
