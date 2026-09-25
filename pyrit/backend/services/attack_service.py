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
    ConverterConfigurationRequest,
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
from pyrit.backend.services.converter_service import get_converter_service
from pyrit.backend.services.media_persistence import persist_media_value_async
from pyrit.backend.services.pagination import (
    decode_keyset_cursor,
    encode_keyset_cursor,
    fingerprint_filters,
    normalize_label_filters,
)
from pyrit.backend.services.target_service import get_target_service
from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.utils import to_sha256
from pyrit.memory import (
    AttackResultKeysetCursor,
    CentralMemory,
    data_serializer_factory,
    set_message_piece_sha256_async,
)
from pyrit.memory.memory_interface import AttackStateConflictError
from pyrit.models import (
    MEDIA_PATH_DATA_TYPES,
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    Conversation,
    ConversationStats,
    ConverterIdentifier,
    Message,
    MessagePiece,
    TargetIdentifier,
)
from pyrit.models.messages.tool_content import validate_tool_conversation
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
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
        target_identifier = await self._get_save_target_async(request.target_registry_name)
        copied: Sequence[MessagePiece] = []
        if request.source_conversation_id is not None and request.cutoff_index is not None:
            conversation, copied = await asyncio.to_thread(
                self._prepare_conversation_up_to,
                source_conversation_id=request.source_conversation_id,
                cutoff_index=request.cutoff_index,
                target_identifier=target_identifier,
            )
            for piece in copied:
                if piece.api_role == "assistant":
                    piece.role = "simulated_assistant"
        else:
            conversation = Conversation(conversation_id=str(uuid.uuid4()), target_identifier=target_identifier)
        attack_result = self._new_manual_attack(
            request=request,
            conversation=conversation,
            objective=request.name or "",
            attack_name=request.name or "ManualAttack",
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
            task = asyncio.create_task(
                asyncio.to_thread(
                    self._memory.add_conversation_branches_to_attack,
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
            labels={"source": "gui", **(request.labels or {})},
            metadata={
                "created_at": now.isoformat(),
                "target_unbound": conversation.target_identifier is None,
                **({"target_registry_name": request.target_registry_name} if request.target_registry_name else {}),
            },
        )

    async def update_attack_async(self, *, attack_result_id: str, request: UpdateAttackRequest) -> AttackSummary | None:
        """
        Update an attack's mutable fields.

        Updates the AttackResult in the database.

        Returns:
            Updated AttackSummary if found, None otherwise.
        """
        results = await asyncio.to_thread(self._memory.get_attack_results, attack_result_ids=[attack_result_id])
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
            if request.expected_objective is not None and existing_objective != request.expected_objective:
                raise AttackObjectiveConflictError("The objective changed. Reload before saving.")
            if existing_objective == request.objective and request.outcome is None:
                return await self.get_attack_async(attack_result_id=attack_result_id)
            update_fields.update(self._objective_update_fields(old=existing_objective, new=request.objective))
        if request.objective is None:
            await asyncio.to_thread(
                self._memory.update_attack_result_by_id,
                attack_result_id=attack_result_id,
                update_fields=update_fields,
            )
            return await self.get_attack_async(attack_result_id=attack_result_id)
        try:
            await asyncio.to_thread(
                self._memory.update_attack_result_conditionally,
                attack_result_id=attack_result_id,
                expected_fields={"objective": results[0].objective},
                update_fields=update_fields,
            )
        except AttackStateConflictError as exc:
            raise AttackObjectiveConflictError(str(exc)) from exc

        return await self.get_attack_async(attack_result_id=attack_result_id)

    async def save_conversation_async(self, *, request: SaveConversationRequest) -> AddMessageResponse:
        """
        Save a complete draft without changing source pieces or invoking a target.

        Returns:
            The stored attack and conversation.
        """
        fingerprint = to_sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True))
        conversation_id = str(request.save_id)
        attack_result_id = (
            str(request.attack_result_id)
            if request.destination == "same_attack"
            else str(uuid.uuid5(request.save_id, "attack"))
        )
        existing = await asyncio.to_thread(self._memory.get_attack_results, attack_result_ids=[attack_result_id])
        if existing and existing[0].metadata.get(f"conversation_save:{conversation_id}") == fingerprint:
            return await self._saved_conversation_response_async(
                attack_result_id=attack_result_id,
                conversation_id=conversation_id,
            )
        source_pieces = await self._get_save_source_async(request=request)
        same_attack = request.destination == "same_attack"
        new_attack: AttackResult | None = None
        expected_fields: dict[str, Any] = {}
        update_fields: dict[str, Any] = {}
        if same_attack:
            attack_result_id = str(request.attack_result_id)
            results = await asyncio.to_thread(self._memory.get_attack_results, attack_result_ids=[attack_result_id])
            if not results:
                raise ValueError("The destination attack does not exist")
            attack = results[0]
            if attack.operator and attack.operator != request.operator:
                raise PermissionError("Cannot save to an attack owned by another operator")
            identifier = attack.get_attack_strategy_identifier()
            target_identifier = identifier.get_child("objective_target") if identifier else None
            if request.target_registry_name:
                selected_target = await self._get_save_target_async(request.target_registry_name)
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
                update_fields = self._objective_update_fields(old=attack.objective, new=request.objective)
        else:
            attack_result_id = str(uuid.uuid5(request.save_id, "attack"))
            target_identifier = await self._get_save_target_async(request.target_registry_name)
            new_attack = self._new_manual_attack(
                request=request,
                attack_result_id=attack_result_id,
                conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier),
                objective=request.objective or "",
            )
        target = await self._validate_editor_target_async(
            target_identifier=target_identifier,
            registry_name=request.target_registry_name,
            data_types={
                piece.converted_value_data_type or piece.data_type
                for message in request.messages
                for piece in message.pieces
            },
        )
        persisted_paths: list[str] = []
        inserted = False
        try:
            pieces = await self._prepare_message_pieces_async(
                messages=request.messages,
                source_pieces=source_pieces,
                conversation_id=conversation_id,
                persisted_paths=persisted_paths,
                target=target,
            )
            save_task = asyncio.create_task(
                asyncio.to_thread(
                    self._memory.add_conversation_branches_to_attack,
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
                    else None,
                )
            )
            try:
                inserted = await asyncio.shield(save_task)
            except asyncio.CancelledError:
                inserted = await save_task
                raise
        finally:
            if not inserted:
                await self._cleanup_saved_media_async(persisted_paths)
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
        results = await asyncio.to_thread(
            self._memory.get_attack_results,
            attack_result_ids=[str(request.source_attack_result_id)],
        )
        if not results or str(request.source_conversation_id) not in results[0].get_active_conversation_ids():
            raise ValueError("The source conversation does not belong to the source attack")
        pieces = await asyncio.to_thread(
            self._memory.get_message_pieces,
            conversation_id=str(request.source_conversation_id),
        )
        return {piece.id: piece for piece in pieces}

    async def _get_save_target_async(self, registry_name: str | None) -> TargetIdentifier | None:
        """
        Resolve an optional target without calling it.

        Returns:
            The target identity, or None for an unbound draft.
        """
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
                if source:
                    request_piece.original_prompt_id = str(source.original_prompt_id)
                converted = request_piece.converted_value
                same_values = source is not None and (
                    request_piece.original_value == source.original_value
                    and request_piece.data_type == source.original_value_data_type
                    and (converted if converted is not None else request_piece.original_value) == source.converted_value
                    and (request_piece.converted_value_data_type or request_piece.data_type)
                    == source.converted_value_data_type
                )
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
                if same_values and source and piece.applied_converter_ids is None:
                    saved.converter_identifiers = list(source.converter_identifiers)
                    saved.response_error = source.response_error
                else:
                    applied = self._resolve_applied_converter_identifiers([request_piece])
                    saved.converter_identifiers = list(applied.get(0, []))
                prepared.append(saved)
                prepared_pieces.append((saved, request_piece))
            prepared_messages.append(Message(message_pieces=prepared))
        if target:
            target.validate_tool_history(prepared_messages)
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
        data_types: set[str],
    ) -> PromptTarget | None:
        """
        Require editable history and support for each structured tool input.

        Returns:
            The resolved target for provider preflight, or None for an unbound draft.

        Raises:
            ValueError: If a bound target is unavailable or cannot replay the draft.
        """
        if target_identifier is None:
            return None
        service = get_target_service()
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
        capabilities = target.capabilities
        if not capabilities.supports_editable_history or not capabilities.supports_multi_turn:
            raise ValueError("The selected target does not support editable history. Select a different target.")
        tool_types = data_types & {"function_call", "function_call_output", "tool_call"}
        unsupported = tool_types - set(capabilities.supported_input_modalities)
        if unsupported:
            missing = ", ".join(sorted(unsupported))
            raise ValueError(f"The selected target does not support these tool pieces: {missing}.")
        target_object = service.get_target_object(target_registry_name=target.target_registry_name)
        if target_object is None:
            raise ValueError("The selected target is no longer registered")
        return target_object

    @staticmethod
    def _objective_update_fields(*, old: str, new: str) -> dict[str, Any]:
        """
        Prepare objective changes without deleting historical scores.

        Returns:
            Fields to update, or an empty mapping when the objective is unchanged.
        """
        if old == new:
            return {}
        return {
            "objective": new,
            "objective_sha256": to_sha256(new),
            "outcome": AttackOutcome.UNDETERMINED.value,
            "outcome_reason": None,
            "automated_score_id": None,
            "human_score_id": None,
        }

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
        applied_converter_identifiers = self._resolve_applied_converter_identifiers(request.pieces)
        if request.send and ar.metadata.get("target_unbound") is True:
            ar = await self._bind_manual_target_async(attack=ar, registry_name=target_registry_name)
        self._validate_target_match(attack_identifier=ar.get_attack_strategy_identifier(), request=request)
        last_response_id: str | None = None

        # Get existing messages to determine sequence.
        # NOTE: This read-then-write is not atomic (TOCTOU). Fine for the
        # current single-user UI, but would need a DB-level sequence
        # generator or optimistic locking if concurrent writes are supported.
        existing = self._memory.get_message_pieces(conversation_id=msg_conversation_id)
        sequence = max((p.sequence for p in existing), default=-1) + 1

        if request.send:
            assert target_registry_name is not None  # validated above
            prior_ids = {p.id for p in existing}
            try:
                await self._send_and_store_message_async(
                    conversation_id=msg_conversation_id,
                    target_registry_name=target_registry_name,
                    request=request,
                    sequence=sequence,
                    request_converter_configurations=request_converter_configs,
                    response_converter_configurations=response_converter_configs,
                    preconverted_indexes=preconverted_indexes,
                    applied_converter_identifiers=applied_converter_identifiers,
                )
            except Exception:
                # PromptNormalizer persists a full error piece (response_error +
                # traceback) to memory *before* re-raising. Surface that stored
                # piece inline so the send (POST) response matches the
                # conversation-reload (GET) view instead of collapsing to a
                # generic 500. If no new error piece was stored (the failure
                # happened before the send, e.g. target lookup), re-raise so the
                # route still reports a real error.
                current_pieces = self._memory.get_message_pieces(conversation_id=msg_conversation_id)
                if not any(p.id not in prior_ids and p.has_error() for p in current_pieces):
                    raise
                logger.exception(
                    "Send failed for attack '%s' conversation '%s'; surfacing stored error piece.",
                    attack_result_id,
                    msg_conversation_id,
                )
            current_pieces = await asyncio.to_thread(
                self._memory.get_message_pieces,
                conversation_id=msg_conversation_id,
            )
            last_response = next(
                (piece for piece in current_pieces if piece.id not in prior_ids and piece.role == "assistant"),
                None,
            )
            last_response_id = str(last_response.id) if last_response else None
        else:
            existing_metadata = self._memory._get_conversation(conversation_id=msg_conversation_id)
            await self._store_message_only_async(
                conversation_id=msg_conversation_id,
                request=request,
                sequence=sequence,
                target_identifier=existing_metadata.target_identifier if existing_metadata else None,
                applied_converter_identifiers=applied_converter_identifiers,
            )

        await self._update_attack_after_message_async(
            attack_result_id=attack_result_id,
            ar=ar,
            last_response_id=last_response_id,
            request_converter_configurations=self._exclude_preconverted_piece_indexes(
                configurations=request_converter_configs,
                preconverted_indexes=preconverted_indexes,
                piece_count=len(request.pieces),
            ),
            response_converter_configurations=response_converter_configs,
            applied_converter_identifiers=applied_converter_identifiers,
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

    async def _bind_manual_target_async(self, *, attack: AttackResult, registry_name: str | None) -> AttackResult:
        """
        Bind an explicitly unbound manual attack before its first send.

        Returns:
            The reloaded, target-bound attack.
        """
        if not registry_name:
            raise ValueError("Select a target before sending")
        target = await self._get_save_target_async(registry_name)
        conversations = [
            await asyncio.to_thread(self._memory.get_conversation_messages, conversation_id=conversation_id)
            for conversation_id in attack.get_active_conversation_ids()
        ]
        target_object = await self._validate_editor_target_async(
            target_identifier=target,
            registry_name=registry_name,
            data_types={
                piece.converted_value_data_type
                for conversation in conversations
                for message in conversation
                for piece in message.message_pieces
            },
        )
        if target_object:
            for conversation in conversations:
                target_object.validate_tool_history(conversation)
        atomic = AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                objective_target=target,
            )
        )
        metadata = {"target_unbound": False, "target_registry_name": registry_name}
        await asyncio.to_thread(
            self._memory.update_attack_result_conditionally,
            attack_result_id=attack.attack_result_id,
            expected_fields={
                "atomic_attack_identifier_hash": attack.atomic_attack_identifier.hash
                if attack.atomic_attack_identifier
                else None
            },
            update_fields={"atomic_attack_identifier": atomic.model_dump(), "attack_metadata": metadata},
            conversation_target=target,
        )
        results = await asyncio.to_thread(self._memory.get_attack_results, attack_result_ids=[attack.attack_result_id])
        if not results:
            raise ValueError("The attack no longer exists")
        return results[0]

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

    async def _update_attack_after_message_async(
        self,
        *,
        attack_result_id: str,
        ar: AttackResult,
        last_response_id: str | None,
        request_converter_configurations: list[ConverterConfiguration],
        response_converter_configurations: list[ConverterConfiguration],
        applied_converter_identifiers: dict[int, list[ConverterIdentifier]],
    ) -> None:
        """
        Update attack recency and converter tracking after a message is added.

        Bumps the attack's ``timestamp`` column (the single indexed recency key) so the edited
        conversation re-floats to the top of the History view.

        Args:
            attack_result_id: The attack result to update.
            ar: The current attack result.
            last_response_id: The latest target response piece ID, if one was stored.
            request_converter_configurations: Resolved request converter configurations used for this message.
            response_converter_configurations: Resolved response converter configurations used for this message.
            applied_converter_identifiers: Registered converters already applied to each preconverted piece.
        """
        update_fields: dict[str, Any] = {"timestamp": datetime.now(UTC)}
        if last_response_id:
            update_fields["last_response_id"] = last_response_id

        request_converter_ids = [
            identifier for identifiers in applied_converter_identifiers.values() for identifier in identifiers
        ]
        request_converter_ids.extend(self._get_converter_identifiers(configurations=request_converter_configurations))
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
    # Private Helper Methods - Duplicate / Branch
    # ========================================================================

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

    @staticmethod
    async def _persist_base64_pieces_async(
        *, pieces: Sequence[MessagePieceRequest], persisted_paths: list[str] | None = None
    ) -> None:
        """
        Resolve original and converted media independently, updating values in-place.

        The frontend sends binary media (images, audio, etc.) as base64 strings
        with a ``*_path`` data_type.  The PyRIT target layer expects ``*_path``
        values to be **file paths**, so we decode the base64 data, write it to
        the results store, and replace the request values with the resulting
        file path before the message is built.

        If the value is already an HTTP(S) URL (e.g. an Azure Blob Storage URL
        from a remixed/copied message), it is kept as-is since the file already
        exists in storage.
        """
        for piece in pieces:
            original_value = piece.original_value
            converted_value = piece.converted_value
            converted_type = piece.converted_value_data_type or piece.data_type
            if piece.data_type in MEDIA_PATH_DATA_TYPES:
                result = await persist_media_value_async(
                    value=original_value,
                    data_type=piece.data_type,
                    mime_type=piece.mime_type,
                    serializer_factory=data_serializer_factory,
                    created_paths=persisted_paths,
                )
                if result.resolved:
                    original_value = result.value
                    if converted_value is None or (
                        converted_value == piece.original_value and converted_type == piece.data_type
                    ):
                        converted_value = original_value

            if (
                converted_value is not None
                and converted_type in MEDIA_PATH_DATA_TYPES
                and (converted_value != original_value or converted_type != piece.data_type)
            ):
                result = await persist_media_value_async(
                    value=converted_value,
                    data_type=converted_type,
                    serializer_factory=data_serializer_factory,
                    created_paths=persisted_paths,
                )
                if result.resolved:
                    converted_value = result.value

            piece.original_value = original_value
            piece.converted_value = converted_value

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
        applied_converter_identifiers: dict[int, list[ConverterIdentifier]],
    ) -> None:
        """Send message to target via normalizer and store response."""
        target_obj = get_target_service().get_target_object(target_registry_name=target_registry_name)
        if not target_obj:
            raise ValueError(f"Target object for '{target_registry_name}' not found")

        await self._persist_base64_pieces_async(pieces=request.pieces)

        self._resolve_video_remix_metadata(request)

        pyrit_message = request_to_pyrit_message(
            request=request,
            conversation_id=conversation_id,
            sequence=sequence,
        )
        for index, identifiers in applied_converter_identifiers.items():
            pyrit_message.message_pieces[index].converter_identifiers.extend(identifiers)

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
        applied_converter_identifiers: dict[int, list[ConverterIdentifier]],
        target_identifier: ComponentIdentifier | None = None,
    ) -> None:
        """Store message without sending (send=False)."""
        await self._persist_base64_pieces_async(pieces=request.pieces)
        self._memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier)
        )
        for index, p in enumerate(request.pieces):
            piece = request_piece_to_pyrit_message_piece(
                piece=p,
                role=request.role,
                conversation_id=conversation_id,
                sequence=sequence,
            )
            piece.converter_identifiers.extend(applied_converter_identifiers.get(index, []))
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
    def _resolve_applied_converter_identifiers(
        pieces: list[MessagePieceRequest],
    ) -> dict[int, list[ConverterIdentifier]]:
        """
        Resolve client-reported execution order without inferring type transitions.

        Returns:
            Registry-validated converter identifiers by message piece index, preserving order and duplicates.
        """
        return {
            index: [
                ConverterIdentifier.from_component_identifier(converter.get_identifier())
                for converter in get_converter_service().get_converter_objects_for_ids(
                    converter_ids=piece.applied_converter_ids
                )
            ]
            for index, piece in enumerate(pieces)
            if piece.applied_converter_ids
        }

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
