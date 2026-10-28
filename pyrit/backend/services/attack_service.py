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
import json
import logging
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
    MessageRequest,
    MessageView,
    PrependedMessageRequest,
    SaveConversationRequest,
    TargetResponseStatus,
    UpdateAttackRequest,
    UpdateMainConversationRequest,
    UpdateMainConversationResponse,
)
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.message_sends import MessageSendRequest, MessageSendStatus
from pyrit.backend.services.chat_scoring_service import get_chat_scoring_service
from pyrit.backend.services.component_lifecycle import release_component_async
from pyrit.backend.services.history_replay import validate_history_replay
from pyrit.backend.services.media_persistence import persist_message_pieces_async
from pyrit.backend.services.message_send_service import (
    MessageSendService,
    get_message_send_service,
    resolve_applied_converter_identifiers,
)
from pyrit.backend.services.pagination import (
    decode_keyset_cursor,
    encode_keyset_cursor,
    fingerprint_filters,
    normalize_label_filters,
)
from pyrit.backend.services.saved_conversation_read_service import SavedConversationReadService
from pyrit.backend.services.target_service import get_target_service
from pyrit.common.utils import to_sha256
from pyrit.executor.attack import attack_outcome_from_score
from pyrit.memory import (
    AttackResultKeysetCursor,
    CentralMemory,
    MemoryInterface,
    data_serializer_factory,
    set_message_piece_sha256_async,
)
from pyrit.memory.memory_interface import AttackStateConflictError
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackResultSelection,
    ComponentIdentifier,
    Conversation,
    ConversationStats,
    Message,
    MessagePiece,
    TargetIdentifier,
    group_conversation_message_pieces_by_sequence,
)
from pyrit.models.component_spec import RegisteredComponentReference, TargetBinding
from pyrit.models.messages.tool_content import validate_tool_conversation
from pyrit.models.results.attack_result import AttackScoringInputs
from pyrit.models.target.request_trace_context import RequestTraceContext
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


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
    """The shared objective changed while it was being edited."""


class AttackService:
    """
    Service for managing attacks.

    Uses PyRIT memory (database) as the source of truth via AttackResult.
    """

    def __init__(
        self, *, message_send_service: MessageSendService | None = None, memory: MemoryInterface | None = None
    ) -> None:
        """Initialize the attack service."""
        self._memory = memory if memory is not None else CentralMemory.get_memory_instance()
        self._saved_conversation_reader = SavedConversationReadService(memory=self._memory)
        self._message_send_service = (
            message_send_service if message_send_service is not None else get_message_send_service()
        )

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
        operation_ids: Sequence[uuid.UUID] | None = None,
        min_turns: int | None = None,
        max_turns: int | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> AttackListResponse:
        """
        List attacks with optional filtering and pagination.

        Queries every saved result ID, including results sharing a main conversation.

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
            operation_ids: Filter by saved Operation IDs. Legacy attacks that only carry an
                operation name never match.
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
        # set. The memory layer applies the turn bounds, orders by recency, seeks
        # past the anchor, and limits in SQL, so only one page's worth of rows is materialized
        # instead of the full table.
        result_selection = AttackResultSelection.ALL_RESULTS
        normalized_labels = normalize_label_filters(labels=labels)
        fingerprint_values: dict[str, Any] = {
            "result_selection": result_selection.value,
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
        if operation_ids is not None:
            fingerprint_values["operation_ids"] = [str(operation_id) for operation_id in operation_ids]
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
        results = await self._memory.get_attack_results_async(
            result_selection=result_selection,
            outcome=outcome,
            operator=operator,
            operation=operation,
            operation_ids=operation_ids,
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

        stats_map = (
            (await self._memory.get_conversation_stats_async(conversation_ids=list(all_conv_ids)))
            if all_conv_ids
            else {}
        )

        # Phase 3: Build summaries from aggregated stats for the page
        operation_names = await self._get_operation_names_async(results=page_results)
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

            page.append(
                await attack_result_to_summary_async(
                    ar,
                    stats=merged,
                    operation_name=operation_names.get(ar.operation_id) if ar.operation_id else None,
                    scoring_inputs=get_chat_scoring_service().inputs(ar),
                    execution_read_only=get_chat_scoring_service().execution_read_only(ar),
                )
            )

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
        return await self._memory.get_unique_attack_class_names_async()

    async def get_converter_options_async(self) -> list[str]:
        """
        Get all unique converter type names used across attack results.

        Delegates to the memory layer which extracts distinct converter
        type names from the atomic_attack_identifier JSON column via SQL.

        Returns:
            Sorted list of unique converter type names.
        """
        return await self._memory.get_unique_converter_class_names_async()

    async def get_attack_async(self, *, attack_result_id: str) -> AttackSummary | None:
        """
        Get attack details (high-level metadata, no messages).

        Queries the AttackResult from the database by its primary key.

        Returns:
            AttackSummary if found, None otherwise.
        """
        results = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if not results:
            return None

        ar = results[0]
        stats_map = await self._memory.get_conversation_stats_async(conversation_ids=[ar.conversation_id])
        stats = stats_map.get(ar.conversation_id, ConversationStats(message_count=0))
        operation_names = await self._get_operation_names_async(results=[ar])
        return await attack_result_to_summary_async(
            ar,
            stats=stats,
            operation_name=operation_names.get(ar.operation_id) if ar.operation_id else None,
            scoring_inputs=get_chat_scoring_service().inputs(ar),
            execution_read_only=get_chat_scoring_service().execution_read_only(ar),
        )

    async def _get_operation_names_async(self, *, results: Sequence[AttackResult]) -> dict[uuid.UUID, str]:
        """
        Read the current names of the saved Operations referenced by these attacks.

        Returns:
            dict[uuid.UUID, str]: Current names keyed by Operation ID.
        """
        operation_ids = {ar.operation_id for ar in results if ar.operation_id is not None}
        if not operation_ids:
            return {}
        operations = await self._memory.get_operations_async(operation_ids=list(operation_ids))
        return {operation.id: operation.name for operation in operations}

    async def get_conversation_messages_async(
        self,
        *,
        attack_result_id: str,
        conversation_id: str,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> ConversationMessagesResponse | None:
        """
        Get messages for a main or pruned conversation belonging to an attack.

        Args:
            attack_result_id: The AttackResult's primary key (used to verify existence).
            conversation_id: The conversation whose messages to return.
            limit: Optional message-piece page size. None preserves the existing full-conversation read.
            cursor: Continuation for an explicitly paged read.

        Returns:
            ConversationMessagesResponse if attack found, None otherwise.

        Raises:
            ValueError: If the conversation does not belong to the attack.
        """
        if limit is not None:
            return await self._saved_conversation_reader.get_messages_async(
                attack_result_id=attack_result_id, conversation_id=conversation_id, limit=limit, cursor=cursor
            )
        if cursor is not None:
            raise ValueError("A message-page cursor requires a limit.")

        # Check attack exists
        results = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if not results:
            return None

        # Verify the conversation belongs to this attack
        ar = results[0]
        if conversation_id not in ar.get_active_conversation_ids():
            raise ValueError(f"Conversation '{conversation_id}' is not part of attack '{attack_result_id}'")

        # Get messages for this conversation
        pyrit_messages = await self._memory.get_conversation_messages_async(conversation_id=conversation_id)
        backend_messages = await pyrit_messages_to_dto_async(
            list(pyrit_messages),
            objective_score_id=ar.last_score.id if ar.last_score else None,
            memory=self._memory,
        )
        scoring_result = ar.get_scoring_failure(conversation_id)
        latest_response = next(
            (message for message in reversed(pyrit_messages) if message.api_role == "assistant"), None
        )
        if scoring_result is not None and (
            latest_response is None
            or scoring_result.response_id not in {str(piece.id) for piece in latest_response.message_pieces}
        ):
            scoring_result = None

        return ConversationMessagesResponse(
            conversation_id=conversation_id,
            messages=backend_messages,
            target_response_status=_get_latest_target_response_status(backend_messages),
            scoring_failure=scoring_result,
        )

    async def get_saved_conversation_async(self, *, conversation_id: str) -> AddMessageResponse | None:
        """
        Resolve a saved chat conversation by ID without changing its history.

        Returns:
            The stored attack and conversation, or None if no saved chat owns the ID.
        """
        conversation = await self._memory.get_conversation_metadata_async(conversation_id=conversation_id)
        attack_result_id = conversation.attack_result_id if conversation else None
        if attack_result_id is None:
            legacy_attack = await self._memory.get_attack_result_for_conversation_async(conversation_id=conversation_id)
            if legacy_attack is None:
                return None
            attack_result_id = legacy_attack.attack_result_id
        attack = await self.get_attack_async(attack_result_id=attack_result_id)
        if attack is None or conversation_id not in attack.get_active_conversation_ids():
            return None
        messages = await self.get_conversation_messages_async(
            attack_result_id=attack_result_id, conversation_id=conversation_id
        )
        if messages is None:
            return None
        return AddMessageResponse(attack=attack, messages=messages)

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
        operation_id = await self._memory.resolve_operation_id_async(
            operation_id=request.operation_id, operation_name=request.operation
        )
        # TODO(PyRIT 1.4): Store only the ID with Operation name attribution; an explicit ID wins until then.
        request = request.model_copy(
            update={
                "operation_id": operation_id,
                "operation": request.operation if request.operation_id is None else None,
            }
        )
        target_identifier = await self._get_save_target_async(
            registry_name=request.target_registry_name, binding=request.target_binding
        )
        copied: Sequence[MessagePiece] = []
        if request.source_conversation_id is not None and request.cutoff_index is not None:
            conversation, copied = await self._prepare_conversation_up_to_async(
                source_conversation_id=request.source_conversation_id,
                cutoff_index=request.cutoff_index,
                target_identifier=target_identifier,
            )
        else:
            conversation = Conversation(conversation_id=str(uuid.uuid4()), target_identifier=target_identifier)
        attack_result = self._new_manual_attack(
            request=request,
            conversation=conversation,
            objective=request.name or "",
            attack_name=request.name or "ManualAttack",
        )
        if request.source_conversation_id is not None:
            source_conversation = await self._memory.get_conversation_metadata_async(
                conversation_id=str(request.source_conversation_id)
            )
            source = (
                await self._memory.get_attack_results_async(attack_result_ids=[source_conversation.attack_result_id])
                if source_conversation and source_conversation.attack_result_id
                else []
            )
            if source:
                get_chat_scoring_service().continue_manually(
                    source=source[0],
                    destination=attack_result,
                    source_conversation_id=str(request.source_conversation_id),
                    retained_pieces=copied,
                    explicit_selection="chat_scorer" in request.model_fields_set,
                )
        prepended = list(request.prepended_conversation or [])
        if request.system_prompt:
            prepended.insert(
                0,
                PrependedMessageRequest(
                    role="system",
                    pieces=[MessagePieceRequest(original_value=request.system_prompt)],
                ),
            )
        persisted_paths: list[str] = []
        inserted = False
        try:
            pieces = await self._prepare_message_pieces_async(
                messages=prepended,
                conversation_id=conversation.conversation_id,
                persisted_paths=persisted_paths,
                start_sequence=max((piece.sequence for piece in copied), default=-1) + 1,
            )
            if copied or pieces:
                target_object = await self._validate_editor_target_async(
                    target_identifier=target_identifier,
                    registry_name=request.target_registry_name,
                    binding=request.target_binding,
                )
                try:
                    if target_object:
                        validate_history_replay(
                            target=target_object,
                            messages=group_conversation_message_pieces_by_sequence([*copied, *pieces]),
                        )
                finally:
                    if request.target_binding and target_object:
                        await release_component_async(target_object)
            task = asyncio.create_task(
                self._memory.add_conversation_branches_to_attack_async(
                    attack_result_id=attack_result.attack_result_id,
                    conversations=[conversation],
                    message_pieces=[*copied, *pieces],
                    new_attack=attack_result,
                )
            )
            try:
                inserted = await asyncio.shield(task)
            except asyncio.CancelledError:
                inserted = await task
                raise
            if not inserted:
                raise AttackStateConflictError("The attack could not be created")
        finally:
            if not inserted:
                await self._cleanup_saved_media_async(persisted_paths)
        return CreateAttackResponse(
            attack_result_id=attack_result.attack_result_id,
            conversation_id=conversation.conversation_id,
            created_at=attack_result.timestamp,
        )

    @staticmethod
    def _new_manual_attack(
        *,
        request: CreateAttackRequest | SaveConversationRequest,
        conversation: Conversation,
        objective: str,
        attack_result_id: str | None = None,
        attack_name: str = "ManualAttack",
    ) -> AttackResult:
        """
        Build manual attack metadata for normal creation and editor saves.

        Returns:
            The attack ready for persistence with its conversation.
        """
        now = datetime.now(UTC)
        return AttackResult(
            attack_result_id=attack_result_id or str(uuid.uuid4()),
            conversation_id=conversation.conversation_id,
            objective=objective,
            atomic_attack_identifier=AtomicAttackIdentifier.build(
                attack_identifier=AttackIdentifier(
                    class_name=attack_name,
                    class_module="pyrit.backend",
                    objective_target=TargetIdentifier.from_component_identifier(conversation.target_identifier)
                    if conversation.target_identifier
                    else None,
                )
            ),
            outcome=AttackOutcome.UNDETERMINED,
            timestamp=now,
            operator=request.operator,
            operation=request.operation,
            operation_id=request.operation_id,
            labels={"source": "gui", **(request.labels or {})},
            metadata={
                "created_at": now.isoformat(),
                "target_unbound": conversation.target_identifier is None,
                **({"target_registry_name": request.target_registry_name} if request.target_registry_name else {}),
                **(request.target_binding.to_metadata() if request.target_binding else {}),
                **(
                    AttackScoringInputs(scorer=request.chat_scorer, revision=str(uuid.uuid4())).to_metadata()
                    if "chat_scorer" in request.model_fields_set
                    else {}
                ),
            },
        )

    async def update_attack_async(self, *, attack_result_id: str, request: UpdateAttackRequest) -> AttackSummary | None:
        """
        Update an attack's mutable fields.

        Updates the AttackResult in the database.

        Returns:
            Updated AttackSummary if found, None otherwise.
        """
        results = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if not results:
            return None

        if request.objective is not None or "chat_scorer" in request.model_fields_set:
            get_chat_scoring_service().require_manual(results[0])
        update_fields: dict[str, Any] = {"timestamp": datetime.now(UTC)}
        expected_fields: dict[str, Any] = {"objective": results[0].objective}
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
            if request.expected_objective is not None and existing_objective != request.expected_objective:
                raise AttackObjectiveConflictError("The objective changed. Reload before saving.")
            if (
                existing_objective == request.objective
                and request.outcome is None
                and "chat_scorer" not in request.model_fields_set
            ):
                return await self.get_attack_async(attack_result_id=attack_result_id)
            update_fields.update(
                self._objective_update_fields(
                    attack=results[0],
                    new=request.objective,
                    default_scorer=request.default_chat_scorer
                    if "chat_scorer" not in request.model_fields_set
                    else None,
                )
            )
        if "chat_scorer" in request.model_fields_set:
            if request.chat_scorer is not None:
                get_chat_scoring_service().resolve(request.chat_scorer)
            update_fields["attack_metadata"] = {
                **update_fields.get("attack_metadata", {}),
                **AttackScoringInputs(scorer=request.chat_scorer, revision=str(uuid.uuid4())).to_metadata(),
            }
        if "attack_metadata" in update_fields:
            expected_fields["attack_metadata"] = results[0].metadata
        if request.objective is None and "chat_scorer" not in request.model_fields_set:
            await self._memory.update_attack_result_by_id_async(
                attack_result_id=attack_result_id,
                update_fields=update_fields,
            )
            return await self.get_attack_async(attack_result_id=attack_result_id)
        try:
            await self._memory.update_attack_result_conditionally_async(
                attack_result_id=attack_result_id,
                expected_fields=expected_fields,
                update_fields=update_fields,
            )
        except AttackStateConflictError as exc:
            raise AttackObjectiveConflictError(str(exc)) from exc

        return await self.get_attack_async(attack_result_id=attack_result_id)

    async def save_conversation_async(self, *, request: SaveConversationRequest) -> AddMessageResponse:
        """
        Save a complete draft without invoking a target or replacing sent evidence.

        Returns:
            The stored attack and conversation.
        """
        if not request.reuse_unsent_conversation:
            return await self._save_conversation_async(request=request)
        with self._message_send_service.reserve_conversation(str(request.source_conversation_id)):
            return await self._save_conversation_async(request=request)

    async def _save_conversation_async(self, *, request: SaveConversationRequest) -> AddMessageResponse:
        """
        Prepare and persist a conversation while the caller holds any required source reservation.

        Returns:
            The stored attack and conversation.
        """
        if request.destination == "new_attack":
            operation_id = await self._memory.resolve_operation_id_async(
                operation_id=request.operation_id, operation_name=request.operation
            )
            # TODO(PyRIT 1.4): Store only the ID with Operation name attribution; an explicit ID wins until then.
            request = request.model_copy(
                update={
                    "operation_id": operation_id,
                    "operation": request.operation if request.operation_id is None else None,
                }
            )
        fingerprint_values = request.model_dump(mode="json", exclude={"default_chat_scorer"})
        if request.default_chat_scorer is not None:
            fingerprint_values["default_chat_scorer"] = request.default_chat_scorer.model_dump(mode="json")
        if "chat_scorer" in request.model_fields_set and request.chat_scorer is None:
            fingerprint_values["chat_scorer_explicitly_off"] = True
        fingerprint = to_sha256(json.dumps(fingerprint_values, sort_keys=True))
        conversation_id = str(request.save_id)
        attack_result_id = (
            str(request.attack_result_id)
            if request.destination == "same_attack"
            else str(uuid.uuid5(request.save_id, "attack"))
        )
        existing = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if existing and (
            existing[0].metadata.get(f"conversation_save:{conversation_id}") == fingerprint
            or existing[0].metadata.get(f"conversation_save:{conversation_id}:{fingerprint}")
        ):
            return await self._saved_conversation_response_async(
                attack_result_id=attack_result_id,
                conversation_id=conversation_id,
            )
        if (
            request.reuse_unsent_conversation
            and existing
            and (
                existing[0].metadata.get(f"conversation_save:{request.source_conversation_id}") == fingerprint
                or existing[0].metadata.get(f"conversation_save:{request.source_conversation_id}:{fingerprint}")
            )
        ):
            return await self._saved_conversation_response_async(
                attack_result_id=attack_result_id,
                conversation_id=str(request.source_conversation_id),
            )
        source_pieces = await self._get_save_source_async(request=request)
        replacements: dict[str, Sequence[MessagePiece]] | None = None
        if request.reuse_unsent_conversation:
            if set(source_pieces) != set(request.expected_source_piece_ids or []):
                raise AttackStateConflictError("The conversation changed. Reload before leaving edit mode.")
            if not any(
                piece.role in ("assistant", "tool")
                or piece.prompt_metadata.get(RequestTraceContext.REQUEST_METADATA_KEY)
                or piece.prompt_metadata.get(RequestTraceContext.METADATA_KEY)
                for piece in source_pieces.values()
            ):
                conversation_id = str(request.source_conversation_id)
                replacements = {conversation_id: list(source_pieces.values())}
        same_attack = request.destination == "same_attack"
        replay_registry_name = request.target_registry_name
        new_attack: AttackResult | None = None
        expected_fields: dict[str, Any] = {}
        update_fields: dict[str, Any] = {}
        binding = request.target_binding
        if same_attack:
            attack_result_id = str(request.attack_result_id)
            results = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
            if not results:
                raise ValueError("The destination attack does not exist")
            attack = results[0]
            get_chat_scoring_service().require_manual(attack)
            if request.operation_id is not None and request.operation_id != attack.operation_id:
                raise ValueError("Operation attribution is immutable on continuation")
            original_registry_name = attack.metadata.get("target_registry_name")
            if isinstance(original_registry_name, str):
                replay_registry_name = original_registry_name
            if attack.operator and attack.operator != request.operator:
                raise PermissionError("Cannot save to an attack owned by another operator")
            identifier = attack.get_attack_strategy_identifier()
            target_identifier = identifier.get_child("objective_target") if identifier else None
            saved_binding = TargetBinding.from_metadata(attack.metadata)
            if request.target_binding is not None and request.target_binding != saved_binding:
                raise ValueError("Same attack must keep its temperature. Choose New attack to change it.")
            binding = saved_binding
            if request.target_registry_name:
                selected_target = await self._get_save_target_async(
                    registry_name=request.target_registry_name, binding=binding
                )
                if (
                    target_identifier is None
                    or selected_target is None
                    or selected_target.hash != target_identifier.hash
                ):
                    raise ValueError("Same attack must keep its target. Choose New attack for a different target.")
            expected_fields = {"operator": attack.operator}
            if attack.atomic_attack_identifier:
                expected_fields["atomic_attack_identifier_hash"] = attack.atomic_attack_identifier.hash
            if request.objective is not None and request.objective != attack.objective:
                expected_fields["objective"] = (
                    request.expected_objective if request.expected_objective is not None else attack.objective
                )
                update_fields = self._objective_update_fields(
                    attack=attack, new=request.objective, default_scorer=request.default_chat_scorer
                )
                expected_fields["attack_metadata"] = attack.metadata
        else:
            attack_result_id = str(uuid.uuid5(request.save_id, "attack"))
            target_identifier = await self._get_save_target_async(
                registry_name=request.target_registry_name, binding=request.target_binding
            )
            new_attack = self._new_manual_attack(
                request=request,
                attack_result_id=attack_result_id,
                conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier),
                objective=request.objective or "",
            )
            if request.source_attack_result_id is not None:
                [source] = await self._memory.get_attack_results_async(
                    attack_result_ids=[str(request.source_attack_result_id)]
                )
                retained_ids = {
                    piece.source_piece_id
                    for message in request.messages
                    for piece in message.pieces
                    if piece.source_piece_id is not None
                }
                get_chat_scoring_service().continue_manually(
                    source=source,
                    destination=new_attack,
                    source_conversation_id=str(request.source_conversation_id),
                    retained_pieces=[piece for piece in source_pieces.values() if piece.id in retained_ids],
                    explicit_selection="chat_scorer" in request.model_fields_set,
                )
        target = await self._validate_editor_target_async(
            target_identifier=target_identifier,
            registry_name=replay_registry_name,
            binding=binding,
        )
        persisted_paths: list[str] = []
        inserted = False
        save_failed = False
        try:
            pieces = await self._prepare_message_pieces_async(
                messages=request.messages,
                source_pieces=source_pieces,
                conversation_id=conversation_id,
                persisted_paths=persisted_paths,
                target=target,
            )
            save_task = asyncio.create_task(
                self._memory.add_conversation_branches_to_attack_async(
                    attack_result_id=attack_result_id,
                    conversations=[Conversation(conversation_id=conversation_id, target_identifier=target_identifier)],
                    message_pieces=pieces,
                    request_fingerprint=fingerprint,
                    new_attack=new_attack,
                    expected_fields=expected_fields,
                    update_fields=update_fields,
                    source_conversation=Conversation(
                        conversation_id=str(request.source_conversation_id), target_identifier=target_identifier
                    )
                    if same_attack
                    and request.source_conversation_id
                    and request.source_attack_result_id == request.attack_result_id
                    and replacements is None
                    else None,
                    replace_conversation_pieces=replacements,
                )
            )
            try:
                inserted = await asyncio.shield(save_task)
            except asyncio.CancelledError:
                inserted = await save_task
                raise
        except BaseException:
            save_failed = True
            raise
        finally:
            target_cleanup_failed = False
            try:
                if binding and target:
                    try:
                        await release_component_async(target)
                    except Exception:
                        target_cleanup_failed = True
                        if not save_failed:
                            raise
                        logger.exception("Failed to close temporary target after a conversation save failure")
            finally:
                if not inserted:
                    try:
                        await self._cleanup_saved_media_async(persisted_paths)
                    except Exception:
                        if not save_failed and not target_cleanup_failed:
                            raise
                        logger.exception(
                            "Failed to roll back media after a conversation save or target cleanup failure"
                        )
        return await self._saved_conversation_response_async(
            attack_result_id=attack_result_id,
            conversation_id=conversation_id,
        )

    async def _cleanup_saved_media_async(self, paths: list[str]) -> None:
        """Remove only files created for a failed or duplicate save attempt."""
        if not paths:
            return
        storage = self._memory.results_storage_io
        if storage is None:
            raise RuntimeError("Storage is not configured for draft media cleanup")
        for path in paths:
            try:
                await storage.delete_file_async(path)
            except Exception:
                logger.exception("Failed to clean media created for an unsaved conversation: %s", path)
                raise

    async def _saved_conversation_response_async(
        self, *, attack_result_id: str, conversation_id: str
    ) -> AddMessageResponse:
        """
        Reload a committed draft, including retries after a lost response.

        Returns:
            The stored attack and conversation.
        """
        attack_summary = await self.get_attack_async(attack_result_id=attack_result_id)
        messages = await self.get_conversation_messages_async(
            attack_result_id=attack_result_id,
            conversation_id=conversation_id,
        )
        if attack_summary is None or messages is None:
            raise ValueError("The saved conversation could not be reloaded")
        return AddMessageResponse(attack=attack_summary, messages=messages)

    async def _get_save_source_async(self, *, request: SaveConversationRequest) -> dict[uuid.UUID, MessagePiece]:
        """
        Load source pieces only from a verified, visible conversation.

        Returns:
            Source pieces keyed by their immutable IDs.
        """
        if request.source_conversation_id is None:
            return {}
        results = await self._memory.get_attack_results_async(
            attack_result_ids=[str(request.source_attack_result_id)],
        )
        if not results or str(request.source_conversation_id) not in results[0].get_active_conversation_ids():
            raise ValueError("The source conversation does not belong to the source attack")
        pieces = await self._memory.get_message_pieces_async(
            conversation_id=str(request.source_conversation_id),
        )
        return {piece.id: piece for piece in pieces}

    async def _get_save_target_async(
        self, *, registry_name: str | None, binding: TargetBinding | None = None
    ) -> TargetIdentifier | None:
        """
        Resolve an optional target without calling it.

        Returns:
            The target identity, or None for an unbound draft.
        """
        if binding is not None:
            target = await get_target_service().resolve_binding_async(binding)
            try:
                return TargetIdentifier.from_component_identifier(target.get_identifier())
            finally:
                await release_component_async(target)
        if registry_name is None:
            return None
        service = get_target_service()
        if await service.get_target_async(target_registry_name=registry_name) is None:
            raise ValueError(f"Target instance '{registry_name}' not found")
        target = service.get_target_object(target_registry_name=registry_name)
        if target is None:
            raise ValueError("The selected target is no longer registered")
        return TargetIdentifier.from_component_identifier(target.get_identifier())

    async def _prepare_message_pieces_async(
        self,
        *,
        messages: Sequence[MessageRequest],
        conversation_id: str,
        persisted_paths: list[str],
        source_pieces: dict[uuid.UUID, MessagePiece] | None = None,
        target: PromptTarget | None = None,
        start_sequence: int = 0,
    ) -> list[MessagePiece]:
        """
        Prepare new identities and preserve verified source content and provenance.

        Returns:
            Ordered pieces ready for one transaction.
        """
        prepared_messages: list[Message] = []
        prepared_pieces: list[tuple[MessagePiece, MessagePieceRequest]] = []
        for sequence, message in enumerate(messages, start=start_sequence):
            prepared: list[MessagePiece] = []
            for piece in message.pieces:
                source = (source_pieces or {}).get(piece.source_piece_id) if piece.source_piece_id else None
                if piece.source_piece_id is not None and source is None:
                    raise ValueError("A source piece does not belong to the source conversation")
                if source_pieces is not None and piece.original_prompt_id is not None:
                    raise ValueError("Use source_piece_id; source lineage is assigned by the server")
                request_piece = piece.model_copy(deep=True)
                converted = request_piece.converted_value
                same_values = source is not None and (
                    request_piece.original_value == source.original_value
                    and request_piece.data_type == source.original_value_data_type
                    and (converted if converted is not None else request_piece.original_value) == source.converted_value
                    and (request_piece.converted_value_data_type or request_piece.data_type)
                    == source.converted_value_data_type
                    and message.role.replace("simulated_", "") == source.api_role
                )
                if source:
                    request_piece.original_prompt_id = str(source.original_prompt_id) if same_values else None
                if source and same_values:
                    request_piece.prompt_metadata = dict(source.prompt_metadata)
                elif source:
                    request_piece.prompt_metadata = dict(request_piece.prompt_metadata or {})
                saved = request_piece_to_pyrit_message_piece(
                    piece=request_piece,
                    role=message.role,
                    conversation_id=conversation_id,
                    sequence=sequence,
                )
                saved.set_simulated_role()
                saved.prompt_metadata.pop("source_piece_id", None)
                if source:
                    saved.prompt_metadata["source_piece_id"] = str(source.id)
                if same_values and source and piece.applied_converter_ids is None:
                    saved.converter_identifiers = list(source.converter_identifiers)
                    saved.response_error = source.response_error
                else:
                    applied = resolve_applied_converter_identifiers([request_piece])
                    saved.converter_identifiers = list(applied.get(0, []))
                prepared.append(saved)
                prepared_pieces.append((saved, request_piece))
            prepared_messages.append(Message(message_pieces=prepared))
        if target:
            validate_history_replay(target=target, messages=prepared_messages)
        elif source_pieces is not None:
            validate_tool_conversation(prepared_messages)
        for saved, request_piece in prepared_pieces:
            await self._persist_base64_pieces_async(pieces=[request_piece], persisted_paths=persisted_paths)
            saved.original_value = request_piece.original_value
            converted_value = request_piece.converted_value
            saved.converted_value = converted_value if converted_value is not None else saved.original_value
            await set_message_piece_sha256_async(saved)
        return [saved for saved, _ in prepared_pieces]

    async def _validate_editor_target_async(
        self,
        *,
        target_identifier: ComponentIdentifier | None,
        registry_name: str | None,
        binding: TargetBinding | None = None,
    ) -> PromptTarget | None:
        """
        Resolve a registered target; check replay capabilities against the actual history separately.

        Returns:
            The resolved target for provider preflight, or None for an unbound draft.

        Raises:
            ValueError: If a bound target is unavailable or its identity no longer matches.
        """
        if target_identifier is None:
            return None
        service = get_target_service()
        if binding is not None:
            target_object = await service.resolve_binding_async(binding)
            if target_object.get_identifier().hash != target_identifier.hash:
                await release_component_async(target_object)
                raise ValueError("The saved target does not support this conversation")
            return target_object
        target = await service.get_target_async(target_registry_name=registry_name) if registry_name else None
        if not registry_name:
            cursor = None
            while True:
                page = await service.list_targets_async(cursor=cursor)
                target = next((item for item in page.items if item.identifier.hash == target_identifier.hash), None)
                cursor = page.pagination.next_cursor
                if target is not None or not cursor:
                    break
        if target is None or target.identifier.hash != target_identifier.hash:
            raise ValueError("The attack target is not registered. Choose New attack without a target.")
        target_object = service.get_target_object(target_registry_name=target.target_registry_name)
        if target_object is None:
            raise ValueError("The selected target is no longer registered")
        if not isinstance(target_object, PromptTarget):
            raise ValueError("The selected registry entry is not a prompt target")
        return target_object

    @staticmethod
    def _objective_update_fields(
        *, attack: AttackResult, new: str, default_scorer: RegisteredComponentReference | None = None
    ) -> dict[str, Any]:
        """
        Prepare objective changes without deleting historical scores.

        Returns:
            Fields to update, or an empty mapping when the objective is unchanged.
        """
        if attack.objective == new:
            return {}
        settings = get_chat_scoring_service().objective_settings(attack=attack, objective=new, scorer=default_scorer)
        return {
            "objective": new,
            "objective_sha256": to_sha256(new),
            "outcome": AttackOutcome.UNDETERMINED.value,
            "outcome_reason": None,
            "automated_score_id": None,
            "human_score_id": None,
            "attack_metadata": {AttackResult.SCORING_ERRORS_KEY: {}, **(settings or {})},
        }

    async def remove_human_score_async(self, *, attack_result_id: str) -> AttackSummary | None:
        """
        Remove the human-score override from an attack.

        The immutable score remains in memory. The attack outcome falls back to
        its automated true/false score, or to undetermined when none exists.

        Returns:
            Updated AttackSummary if found, None otherwise.
        """
        for _ in range(5):
            results = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
            if not results:
                return None
            attack = results[0]
            automated_score = attack.automated_score
            outcome = (
                AttackOutcome.UNDETERMINED if automated_score is None else attack_outcome_from_score(automated_score)
            )
            try:
                await self._memory.update_attack_result_conditionally_async(
                    attack_result_id=attack_result_id,
                    expected_fields={
                        "automated_score_id": automated_score.id if automated_score else None,
                        "human_score_id": attack.human_score.id if attack.human_score else None,
                    },
                    update_fields={
                        "human_score_id": None,
                        "outcome": outcome.value,
                        "outcome_reason": automated_score.score_rationale
                        if automated_score and outcome is not AttackOutcome.UNDETERMINED
                        else None,
                        "timestamp": datetime.now(UTC),
                    },
                )
            except AttackStateConflictError:
                continue
            return await self.get_attack_async(attack_result_id=attack_result_id)
        raise AttackObjectiveConflictError("Scores kept changing. Refresh before removing the human override.")

    async def get_conversations_async(
        self, *, attack_result_id: str, limit: int | None = None, cursor: str | None = None
    ) -> AttackConversationsResponse | None:
        """
        Get main and pruned conversations belonging to an attack.

        Each entry is enriched with message count, a preview,
        and the earliest message timestamp using a single batched query.

        Returns:
            AttackConversationsResponse if attack found, None otherwise.
        """
        if limit is not None:
            return await self._saved_conversation_reader.list_conversations_async(
                attack_result_id=attack_result_id, limit=limit, cursor=cursor
            )
        if cursor is not None:
            raise ValueError("A conversation-page cursor requires a limit.")
        results = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if not results:
            return None

        # attack_result_id is a unique primary key, so at most one result is returned.
        ar = results[0]

        # Collect all conversation IDs (main + PRUNED related) and fetch stats in one query.
        active_conv_ids = list(ar.get_active_conversation_ids())
        stats_map = await self._memory.get_conversation_stats_async(conversation_ids=active_conv_ids)

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
        results = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if not results:
            return None

        ar = results[0]
        now = datetime.now(UTC)
        get_chat_scoring_service().require_manual(ar)

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
            source_metadata = await self._memory.get_conversation_metadata_async(
                conversation_id=request.source_conversation_id
            )
            source_metadata = source_metadata or Conversation(
                conversation_id=request.source_conversation_id, target_identifier=objective_target
            )
            conversation, all_pieces = await self._prepare_conversation_up_to_async(
                source_conversation_id=request.source_conversation_id,
                cutoff_index=request.cutoff_index,
                target_identifier=source_metadata.target_identifier,
            )
        else:
            conversation = Conversation(
                conversation_id=str(uuid.uuid4()),
                target_identifier=objective_target,
            )

        if all_pieces and conversation.target_identifier is not None:
            binding = TargetBinding.from_metadata(ar.metadata)
            target = await self._validate_editor_target_async(
                target_identifier=conversation.target_identifier,
                registry_name=ar.metadata.get("target_registry_name")
                if isinstance(ar.metadata.get("target_registry_name"), str)
                else None,
                binding=binding,
            )
            try:
                if target:
                    validate_history_replay(
                        target=target, messages=group_conversation_message_pieces_by_sequence(all_pieces)
                    )
            finally:
                if binding and target:
                    await release_component_async(target)
        stored = await self._memory.add_conversation_branches_to_attack_async(
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
        results = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if not results:
            return None

        ar = results[0]
        target_conv_id = request.conversation_id
        get_chat_scoring_service().require_manual(ar)

        # Only user-visible conversations can become the main conversation.
        if target_conv_id not in ar.get_active_conversation_ids():
            raise ValueError(f"Conversation '{target_conv_id}' is not part of this attack")

        now = datetime.now(UTC)
        stored = await self._memory.promote_attack_conversation_async(
            attack_result_id=attack_result_id, conversation_id=target_conv_id
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
        await self._bind_requested_target_async(attack_result_id=attack_result_id, request=request)
        async with self._message_send_service.add_message_context_async(
            attack_result_id=attack_result_id, request=request
        ):
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

    async def submit_message_send_async(
        self, *, attack_result_id: str, request: MessageSendRequest
    ) -> MessageSendStatus:
        """
        Bind saved draft history before admitting an asynchronous manual send.

        Returns:
            The sending service's transient progress handle.
        """
        await self._bind_requested_target_async(attack_result_id=attack_result_id, request=request)
        return await self._message_send_service.submit_async(attack_result_id=attack_result_id, request=request)

    async def _bind_requested_target_async(self, *, attack_result_id: str, request: AddMessageRequest) -> None:
        """Apply the same first-send target binding to both manual-message endpoints."""
        if not request.send:
            return
        results = await self._memory.get_attack_results_async(attack_result_ids=[attack_result_id])
        if results and results[0].metadata.get("target_unbound") is True:
            if request.target_conversation_id not in results[0].get_active_conversation_ids():
                raise ValueError(f"Conversation '{request.target_conversation_id}' is not part of this attack")
            await self._bind_manual_target_async(
                attack=results[0], registry_name=request.target_registry_name, binding=request.target_binding
            )

    async def _bind_manual_target_async(
        self, *, attack: AttackResult, registry_name: str | None, binding: TargetBinding | None = None
    ) -> AttackResult:
        """
        Bind an explicitly unbound manual attack before its first send.

        Returns:
            The reloaded, target-bound attack.
        """
        get_chat_scoring_service().require_manual(attack)
        if not registry_name:
            raise ValueError("Select a target before sending")
        target = await self._get_save_target_async(registry_name=registry_name, binding=binding)
        conversations = {
            conversation_id: await self._memory.get_conversation_messages_async(conversation_id=conversation_id)
            for conversation_id in attack.get_active_conversation_ids()
        }
        target_object = await self._validate_editor_target_async(
            target_identifier=target,
            registry_name=registry_name,
            binding=binding,
        )
        try:
            if target_object:
                for conversation in conversations.values():
                    validate_history_replay(target=target_object, messages=conversation)
        finally:
            if binding and target_object:
                await release_component_async(target_object)
        atomic = AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                objective_target=target,
            )
        )
        metadata = {
            "target_unbound": False,
            "target_registry_name": registry_name,
            **(binding.to_metadata() if binding else {}),
        }
        await self._memory.update_attack_result_conditionally_async(
            attack_result_id=attack.attack_result_id,
            expected_fields={
                "atomic_attack_identifier_hash": attack.atomic_attack_identifier.hash
                if attack.atomic_attack_identifier
                else None
            },
            update_fields={"atomic_attack_identifier": atomic.model_dump(), "attack_metadata": metadata},
            conversation_target=target,
            expected_conversation_pieces={
                conversation_id: [piece for message in messages for piece in message.message_pieces]
                for conversation_id, messages in conversations.items()
            },
        )
        results = await self._memory.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
        if not results:
            raise ValueError("The attack no longer exists")
        return results[0]

    # ========================================================================
    # Private Helper Methods - Duplicate / Branch
    # ========================================================================

    async def _prepare_conversation_up_to_async(
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
        messages = await self._memory.get_conversation_messages_async(conversation_id=source_conversation_id)
        new_id, pieces = await self._memory.duplicate_messages_async(
            messages=[message for message in messages if message.sequence <= cutoff_index]
        )
        for piece in pieces:
            piece.set_simulated_role()
        return Conversation(conversation_id=new_id, target_identifier=target_identifier), pieces

    # ========================================================================
    # Private Helper Methods - Store Messages
    # ========================================================================

    @staticmethod
    async def _persist_base64_pieces_async(
        *, pieces: Sequence[MessagePieceRequest], persisted_paths: list[str] | None = None
    ) -> None:
        """Persist prepared draft media and track files owned by this save."""
        await persist_message_pieces_async(
            pieces=pieces, persisted_paths=persisted_paths, serializer_factory=data_serializer_factory
        )


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
