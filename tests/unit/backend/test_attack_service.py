# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for attack service.

The attack service uses PyRIT memory with AttackResult as the source of truth.
"""

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest
from sqlalchemy import select

import pyrit
import pyrit.backend.services.pagination as backend_pagination
import pyrit.common.pagination as common_pagination
from pyrit.backend.models.attacks import (
    AddMessageRequest,
    AttackSummary,
    ConversationMessagesResponse,
    CreateAttackRequest,
    CreateConversationRequest,
    MessagePieceRequest,
    PrependedMessageRequest,
    UpdateAttackRequest,
    UpdateMainConversationRequest,
)
from pyrit.backend.services.attack_service import (
    AttackObjectiveConflictError,
    AttackService,
    get_attack_service,
)
from pyrit.backend.services.manual_send_scheduler import (
    ManualSendConflictError,
    ManualSendQueueFullError,
    ManualSendScheduler,
)
from pyrit.backend.services.message_send_service import MessageSendService, get_message_send_service
from pyrit.backend.services.pagination import (
    decode_keyset_cursor,
    encode_keyset_cursor,
    fingerprint_filters,
    normalize_label_filters,
)
from pyrit.common.utils import to_sha256
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.memory_models import ConversationEntry
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackOutcome,
    AttackResult,
    ChatMessageRole,
    ComponentIdentifier,
    Conversation,
    ConversationReference,
    ConversationType,
    Message,
    MessagePiece,
    PromptDataType,
    PromptResponseError,
    Score,
    TargetIdentifier,
)
from pyrit.models.conversation_stats import ConversationStats
from pyrit.prompt_normalizer import PromptNormalizer
from unit.backend.mocks import _make_matching_target_mock, make_attack_result, make_mock_memory
from unit.mocks import MockPromptTarget


@pytest.fixture
def mock_memory() -> MagicMock:
    return make_mock_memory()


@pytest.fixture
def attack_service(mock_memory):
    """Create an attack service with mocked memory."""
    with patch.object(CentralMemory, "get_memory_instance", return_value=mock_memory):
        service = AttackService(message_send_service=MessageSendService(scheduler=ManualSendScheduler()))
        yield service


def _make_message(
    *,
    role: ChatMessageRole,
    sequence: int,
    response_error: PromptResponseError = "none",
) -> Message:
    """Create a single-piece text message for conversation response tests."""
    piece = MessagePiece(
        role=role,
        original_value="message",
        converted_value="message",
        original_value_data_type="text",
        converted_value_data_type="text",
        conversation_id="test-id",
        sequence=sequence,
        response_error=response_error,
    )
    return Message(message_pieces=[piece])


def _keyset_side_effect(backing):
    """Return a get_attack_results side_effect simulating a recency-ordered keyset seek.

    ``backing`` must already be in the order memory would return (recency DESC). When an
    ``after`` anchor is supplied, every row up to and including the anchor id is skipped,
    mirroring a seek strictly past the previous page's last row. The memory layer owns the
    real dedup / turn-filter / recency-sort / seek, so the service tests exercise only the
    cursor<->anchor round-trip and the over-fetch (limit + 1) logic.
    """

    def _side_effect(**kwargs):
        after = kwargs.get("after")
        limit = kwargs.get("limit")
        data = list(backing)
        if after is not None:
            ids = [r.attack_result_id for r in data]
            data = data[ids.index(after.attack_result_id) + 1 :] if after.attack_result_id in ids else []
        if limit is not None:
            data = data[:limit]
        return data

    return _side_effect


def _paginated_backing(count: int) -> list[AttackResult]:
    """Build ``count`` attack results with real UUID ids, ordered as memory would return them."""
    return [make_attack_result(conversation_id=f"attack-{i}", attack_result_id=str(uuid.uuid4())) for i in range(count)]


def _cursor_for(result: AttackResult, *, fingerprint: str | None = None) -> str:
    """Encode a keyset cursor anchored at ``result`` for the given (default) filter set.

    Mirrors what ``list_attacks_async`` mints internally, so tests can feed a cursor back
    in without depending on the fingerprint's exact value.
    """
    effective_fingerprint = fingerprint if fingerprint is not None else _attack_filter_fingerprint()
    return encode_keyset_cursor(
        timestamp=result.timestamp,
        identifier=result.attack_result_id,
        fingerprint=effective_fingerprint,
    )


def _attack_filter_fingerprint(
    *,
    attack_types: list[str] | None = None,
    converter_types: list[str] | None = None,
    converter_types_match: str = "all",
    has_converters: bool | None = None,
    include_scenario_attacks: bool = True,
    outcome: str | None = None,
    labels: dict[str, str | list[str]] | None = None,
    min_turns: int | None = None,
    max_turns: int | None = None,
) -> str:
    """Build the fingerprint used by ``AttackService.list_attacks_async``."""
    return fingerprint_filters(
        filters={
            "attack_types": attack_types,
            "converter_types": converter_types,
            "converter_types_match": converter_types_match,
            "has_converters": has_converters,
            "include_scenario_attacks": include_scenario_attacks,
            "outcome": outcome,
            "labels": normalize_label_filters(labels=labels),
            "min_turns": min_turns,
            "max_turns": max_turns,
        }
    )


def make_mock_piece(
    *,
    conversation_id: str,
    role: str = "user",
    sequence: int = 0,
    original_value: str = "test",
    converted_value: str = "test",
    timestamp: datetime | None = None,
):
    """Create a mock message piece."""
    piece = MagicMock()
    piece.id = "piece-id"
    piece.conversation_id = conversation_id
    piece.role = role
    piece.api_role = "assistant" if role in ("assistant", "simulated_assistant") else role
    piece.sequence = sequence
    piece.original_value = original_value
    piece.converted_value = converted_value
    piece.converted_value_data_type = "text"
    piece.original_value_data_type = "text"
    piece.response_error = "none"
    piece.timestamp = timestamp or datetime.now(UTC)
    # MessagePiece no longer carries scores — they are fetched from memory.
    # Pin original_prompt_id so the mapper's score-lookup key is deterministic.
    piece.original_prompt_id = None
    return piece


def make_mock_message(pieces: list):
    """Create a mock message from pieces."""
    msg = MagicMock()
    msg.message_pieces = pieces
    return msg


# ============================================================================
# Init Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestAttackServiceInit:
    """Tests for AttackService initialization."""

    def test_init_gets_memory_instance(self) -> None:
        """Test that init gets the memory instance."""
        with patch.object(CentralMemory, "get_memory_instance") as get_memory:
            mock_memory = MagicMock()
            get_memory.return_value = mock_memory

            service = AttackService()

            assert service._memory is service._message_send_service._memory is PromptNormalizer()._memory is mock_memory

    def test_default_facades_share_message_send_owner(self) -> None:
        first, second = get_attack_service(), AttackService()
        assert first._message_send_service is second._message_send_service is get_message_send_service()

    def test_injected_sender_preserves_scheduler(self) -> None:
        scheduler = ManualSendScheduler()
        sender = MessageSendService(scheduler=scheduler)
        service = AttackService(message_send_service=sender)
        assert service._message_send_service is sender
        assert sender._scheduler is scheduler
        assert sender is not get_message_send_service()

    async def test_shared_shutdown_rejects_synchronous_facade_async(self) -> None:
        service = get_attack_service()
        await get_message_send_service().shutdown_async()
        with pytest.raises(ManualSendQueueFullError, match="shutting down"):
            await service.add_message_async(
                attack_result_id="attack",
                request=AddMessageRequest(
                    pieces=[MessagePieceRequest(original_value="Hello")],
                    target_conversation_id="conversation",
                    send=False,
                ),
            )


# ============================================================================
# List Attacks Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestListAttacks:
    """Tests for list_attacks method."""

    async def test_list_attacks_returns_empty_when_no_attacks(self, attack_service, mock_memory) -> None:
        """Test that list_attacks returns empty list when no AttackResults exist."""
        mock_memory.get_attack_results_async.return_value = []

        result = await attack_service.list_attacks_async()

        assert result.items == []
        assert result.pagination.has_more is False

    async def test_list_attacks_returns_attacks(self, attack_service, mock_memory) -> None:
        """Test that list_attacks returns attacks from AttackResult records."""
        ar = make_attack_result()
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []

        result = await attack_service.list_attacks_async()

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-1"
        assert result.items[0].attack_type == "Test Attack"

    async def test_list_attacks_filters_by_attack_types_exact(self, attack_service, mock_memory) -> None:
        """Test that list_attacks passes attack_types to memory layer."""
        ar1 = make_attack_result(conversation_id="attack-1", name="CrescendoAttack")
        mock_memory.get_attack_results_async.return_value = [ar1]
        mock_memory.get_message_pieces_async.return_value = []

        result = await attack_service.list_attacks_async(attack_types=["CrescendoAttack"])

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-1"
        # Verify attack_types was forwarded to the memory layer as attack_classes
        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["attack_classes"] == ["CrescendoAttack"]

    async def test_list_attacks_attack_types_passed_to_memory(self, attack_service, mock_memory) -> None:
        """Test that attack_types is forwarded to memory as attack_classes for DB-level filtering."""
        mock_memory.get_attack_results_async.return_value = []
        mock_memory.get_message_pieces_async.return_value = []

        await attack_service.list_attacks_async(attack_types=["Crescendo"])

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["attack_classes"] == ["Crescendo"]

    async def test_list_attacks_filters_by_attack_types_multi(self, attack_service, mock_memory) -> None:
        """Test that multiple attack_types are forwarded as a list to memory for OR-matching."""
        mock_memory.get_attack_results_async.return_value = []
        mock_memory.get_message_pieces_async.return_value = []

        await attack_service.list_attacks_async(attack_types=["CrescendoAttack", "ManualAttack"])

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["attack_classes"] == ["CrescendoAttack", "ManualAttack"]

    async def test_list_attacks_attack_types_empty_list_coerced_to_none(self, attack_service, mock_memory) -> None:
        """Test that attack_types=[] is coerced to None before reaching memory (no filter)."""
        mock_memory.get_attack_results_async.return_value = []
        mock_memory.get_message_pieces_async.return_value = []

        await attack_service.list_attacks_async(attack_types=[])

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["attack_classes"] is None

    async def test_list_attacks_coerces_empty_converter_types_to_no_filter(self, attack_service, mock_memory) -> None:
        """converter_types=[] at the service boundary means 'no converter filter'.

        The 'attacks with no converters' intent is expressed via has_converters=False;
        an empty list is coerced to None so route/service/memory stay consistent.
        """
        mock_memory.get_attack_results_async.return_value = []
        mock_memory.get_message_pieces_async.return_value = []

        await attack_service.list_attacks_async(converter_types=[])

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["converter_classes"] is None
        assert call_kwargs["has_converters"] is None

    async def test_list_attacks_forwards_has_converters_true(self, attack_service, mock_memory) -> None:
        """has_converters=True is forwarded to memory."""
        mock_memory.get_attack_results_async.return_value = []
        mock_memory.get_message_pieces_async.return_value = []

        await attack_service.list_attacks_async(has_converters=True)

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["has_converters"] is True

    async def test_list_attacks_forwards_has_converters_false(self, attack_service, mock_memory) -> None:
        """has_converters=False is forwarded to memory."""
        mock_memory.get_attack_results_async.return_value = []
        mock_memory.get_message_pieces_async.return_value = []

        await attack_service.list_attacks_async(has_converters=False)

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["has_converters"] is False

    async def test_list_attacks_forwards_scenario_attack_filter(self, attack_service, mock_memory) -> None:
        """The scenario-attack inclusion flag is forwarded to memory."""
        mock_memory.get_attack_results_async.return_value = []

        await attack_service.list_attacks_async(include_scenario_attacks=False)

        assert mock_memory.get_attack_results_async.call_args.kwargs["include_scenario_attacks"] is False

    async def test_list_attacks_filters_by_converter_types_and_logic(self, attack_service, mock_memory) -> None:
        """Test that list_attacks passes converter_types to memory layer."""
        ar1 = make_attack_result(conversation_id="attack-1", name="Attack One")
        ar1.atomic_attack_identifier = AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name="Attack One",
                class_module="pyrit.backend",
                children={
                    "request_converters": [
                        ComponentIdentifier(
                            class_name="Base64Converter",
                            class_module="pyrit.converters",
                            params={
                                "supported_input_types": ("text",),
                                "supported_output_types": ("text",),
                            },
                        ),
                        ComponentIdentifier(
                            class_name="ROT13Converter",
                            class_module="pyrit.converters",
                            params={
                                "supported_input_types": ("text",),
                                "supported_output_types": ("text",),
                            },
                        ),
                    ],
                },
            ),
        )
        mock_memory.get_attack_results_async.return_value = [ar1]
        mock_memory.get_message_pieces_async.return_value = []

        result = await attack_service.list_attacks_async(converter_types=["Base64Converter", "ROT13Converter"])

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-1"
        # Verify converter_types was forwarded to the memory layer with default "all" mode
        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["converter_classes"] == ["Base64Converter", "ROT13Converter"]
        assert call_kwargs["converter_classes_match"] == "all"

    async def test_list_attacks_converter_match_all_explicit_pushes_to_memory(
        self, attack_service, mock_memory
    ) -> None:
        """Explicit converter_types_match='all' still pushes converter filter to memory."""
        mock_memory.get_attack_results_async.return_value = []
        mock_memory.get_message_pieces_async.return_value = []

        await attack_service.list_attacks_async(
            converter_types=["Base64Converter", "ROT13Converter"],
            converter_types_match="all",
        )

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["converter_classes"] == ["Base64Converter", "ROT13Converter"]
        assert call_kwargs["converter_classes_match"] == "all"

    async def test_list_attacks_converter_match_any_single_converter_pushes_to_memory(
        self, attack_service, mock_memory
    ) -> None:
        """Degenerate case: converter_types_match='any' with one converter still pushes to memory.

        The memory layer ignores the match mode when the list has fewer than 2 entries, but the
        service still forwards the mode verbatim (memory is authoritative for that optimization).
        """
        mock_memory.get_attack_results_async.return_value = []
        mock_memory.get_message_pieces_async.return_value = []

        await attack_service.list_attacks_async(
            converter_types=["Base64Converter"],
            converter_types_match="any",
        )

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["converter_classes"] == ["Base64Converter"]
        assert call_kwargs["converter_classes_match"] == "any"

    async def test_list_attacks_converter_match_any_pushes_to_memory(self, attack_service, mock_memory) -> None:
        """converter_types_match='any' with 2+ converters pushes down to the DB via memory.

        Previously this branch loaded every row matching other filters into Python and filtered
        with a set intersection, which was O(total rows) per query. The OR-matching is now
        expressed as a DB predicate so only matching rows are returned and pagination is honored.
        """
        mock_memory.get_attack_results_async.return_value = []
        mock_memory.get_message_pieces_async.return_value = []

        await attack_service.list_attacks_async(
            converter_types=["Base64Converter", "ROT13Converter"],
            converter_types_match="any",
        )

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["converter_classes"] == ["Base64Converter", "ROT13Converter"]
        assert call_kwargs["converter_classes_match"] == "any"

    async def test_list_attacks_forwards_min_turns(self, attack_service, mock_memory) -> None:
        """min_turns is forwarded to the memory query (filtering now happens in SQL)."""
        mock_memory.get_attack_results_async.return_value = []

        await attack_service.list_attacks_async(min_turns=3)

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["min_turns"] == 3

    async def test_list_attacks_forwards_max_turns(self, attack_service, mock_memory) -> None:
        """max_turns is forwarded to the memory query (filtering now happens in SQL)."""
        mock_memory.get_attack_results_async.return_value = []

        await attack_service.list_attacks_async(max_turns=3)

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["max_turns"] == 3

    async def test_list_attacks_includes_labels_in_summary(self, attack_service, mock_memory) -> None:
        """Test that list_attacks includes labels from conversation stats in summaries."""
        ar = make_attack_result(
            conversation_id="attack-1",
        )
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_stats_async.return_value = {
            "attack-1": ConversationStats(
                message_count=1,
                last_message_preview="test",
                labels={"env": "prod", "team": "red"},
            ),
        }

        result = await attack_service.list_attacks_async()

        assert len(result.items) == 1
        assert result.items[0].labels == {"env": "prod", "team": "red", "test_ar_label": "test_ar_value"}

    async def test_list_attacks_formats_media_preview(self, attack_service, mock_memory) -> None:
        """list_attacks AttackSummary previews must not leak absolute media paths."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]
        path = r"C:\Users\someone\PyRIT\dbdata\prompt-memory-entries\images\1780010098266691.png"
        mock_memory.get_conversation_stats_async.return_value = {
            "attack-1": ConversationStats(
                message_count=1,
                last_message_preview=path,
                last_message_data_type="image_path",
            ),
        }

        result = await attack_service.list_attacks_async()

        assert len(result.items) == 1
        preview = result.items[0].last_message_preview
        assert preview == "[Image: 1780010098266691.png]"
        assert "C:\\" not in (preview or "")

    async def test_list_attacks_filters_by_dedicated_attribution(self, attack_service, mock_memory) -> None:
        """Dedicated attribution filters are passed to indexed memory columns."""
        ar = make_attack_result(conversation_id="attack-canonical")
        ar.operator = "alice"
        ar.operation = "red"

        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_stats_async.side_effect = lambda conversation_ids: {
            cid: ConversationStats(message_count=1) for cid in conversation_ids
        }

        result = await attack_service.list_attacks_async(operator=["alice"], operation=["red"])

        assert len(result.items) == 1
        mock_memory.get_attack_results_async.assert_called_once()
        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["operator"] == ["alice"]
        assert call_kwargs["operation"] == ["red"]

    async def test_list_attacks_forwards_min_and_max_turns(self, attack_service, mock_memory) -> None:
        """Both min_turns and max_turns are forwarded to the memory query."""
        mock_memory.get_attack_results_async.return_value = []

        await attack_service.list_attacks_async(min_turns=2, max_turns=5)

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["min_turns"] == 2
        assert call_kwargs["max_turns"] == 5


# ============================================================================
# Attack Options Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestAttackOptions:
    """Tests for get_attack_options_async method."""

    async def test_returns_empty_when_no_attacks(self, attack_service, mock_memory) -> None:
        """Test that attack options returns empty list when no attacks exist."""
        mock_memory.get_unique_attack_class_names_async.return_value = []

        result = await attack_service.get_attack_options_async()

        assert result == []
        mock_memory.get_unique_attack_class_names_async.assert_called_once()

    async def test_returns_result_from_memory(self, attack_service, mock_memory) -> None:
        """Test that attack options delegates to memory layer."""
        mock_memory.get_unique_attack_class_names_async.return_value = ["CrescendoAttack", "ManualAttack"]

        result = await attack_service.get_attack_options_async()

        assert result == ["CrescendoAttack", "ManualAttack"]
        mock_memory.get_unique_attack_class_names_async.assert_called_once()


# ============================================================================
# Converter Options Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestConverterOptions:
    """Tests for get_converter_options_async method."""

    async def test_returns_empty_when_no_attacks(self, attack_service, mock_memory) -> None:
        """Test that converter options returns empty list when no attacks exist."""
        mock_memory.get_unique_converter_class_names_async.return_value = []

        result = await attack_service.get_converter_options_async()

        assert result == []
        mock_memory.get_unique_converter_class_names_async.assert_called_once()

    async def test_returns_result_from_memory(self, attack_service, mock_memory) -> None:
        """Test that converter options delegates to memory layer."""
        mock_memory.get_unique_converter_class_names_async.return_value = ["Base64Converter", "ROT13Converter"]

        result = await attack_service.get_converter_options_async()

        assert result == ["Base64Converter", "ROT13Converter"]
        mock_memory.get_unique_converter_class_names_async.assert_called_once()


# ============================================================================
# Get Attack Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestGetAttack:
    """Tests for get_attack method."""

    async def test_get_attack_returns_none_for_nonexistent(self, attack_service, mock_memory) -> None:
        """Test that get_attack returns None when AttackResult doesn't exist."""
        mock_memory.get_attack_results_async.return_value = []

        result = await attack_service.get_attack_async(attack_result_id="nonexistent")

        assert result is None

    async def test_get_attack_returns_attack_details(self, attack_service, mock_memory) -> None:
        """Test that get_attack returns attack details from AttackResult."""
        ar = make_attack_result(
            conversation_id="test-id",
            name="My Attack",
        )
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        result = await attack_service.get_attack_async(attack_result_id="test-id")

        assert result is not None
        assert result.conversation_id == "test-id"
        assert result.attack_type == "My Attack"


# ============================================================================
# Get Conversation Messages Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestGetConversationMessages:
    "Tests for get_conversation_messages_async method."

    async def test_get_conversation_messages_returns_none_for_nonexistent(self, attack_service, mock_memory) -> None:
        "Test that get_conversation_messages_async returns None when attack doesn't exist."
        mock_memory.get_attack_results_async.return_value = []

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="nonexistent", conversation_id="any-id"
        )

        assert result is None

    async def test_get_conversation_messages_returns_messages(self, attack_service, mock_memory) -> None:
        "Test that get_conversation_messages_async returns messages for existing attack."
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="test-id", conversation_id="test-id"
        )

        assert result is not None
        assert result.conversation_id == "test-id"
        assert result.messages == []
        assert result.target_response_status is None

    @pytest.mark.parametrize("response_error", ["none", "processing", "blocked"])
    async def test_get_conversation_messages_returns_latest_target_status_async(
        self,
        attack_service,
        mock_memory,
        response_error: PromptResponseError,
    ) -> None:
        """Test that the latest real assistant response is linked to its user request."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = [
            _make_message(role="user", sequence=2),
            _make_message(role="assistant", sequence=3, response_error=response_error),
        ]

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="test-id", conversation_id="test-id"
        )

        assert result is not None
        assert result.target_response_status is not None
        assert result.target_response_status.response_error == response_error
        assert result.target_response_status.request_turn_number == 2
        assert result.target_response_status.response_turn_number == 3

    async def test_get_conversation_messages_ignores_stale_processing_error(self, attack_service, mock_memory) -> None:
        """Test that a later successful response supersedes an earlier processing failure."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = [
            _make_message(role="user", sequence=0),
            _make_message(role="assistant", sequence=1, response_error="processing"),
            _make_message(role="user", sequence=2),
            _make_message(role="assistant", sequence=3),
        ]

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="test-id", conversation_id="test-id"
        )

        assert result is not None
        assert result.target_response_status is not None
        assert result.target_response_status.response_error == "none"
        assert result.target_response_status.request_turn_number == 2
        assert result.target_response_status.response_turn_number == 3

    @pytest.mark.parametrize("latest_role", ["user", "simulated_assistant"])
    async def test_get_conversation_messages_ignores_non_target_latest_message(
        self,
        attack_service,
        mock_memory,
        latest_role: ChatMessageRole,
    ) -> None:
        """Test that user and simulated-assistant history are not classified as target responses."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = [
            _make_message(role="user", sequence=0),
            _make_message(role=latest_role, sequence=1, response_error="processing"),
        ]

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="test-id", conversation_id="test-id"
        )

        assert result is not None
        assert result.target_response_status is None

    async def test_get_conversation_messages_marks_attack_objective_score(self, attack_service, mock_memory) -> None:
        """The message mapper receives the attack's canonical objective score ID."""
        ar = make_attack_result(conversation_id="test-id")
        objective_score_id = uuid.uuid4()
        ar.automated_score = MagicMock(id=objective_score_id)
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        with patch(
            "pyrit.backend.services.attack_service.pyrit_messages_to_dto_async",
            new=AsyncMock(return_value=[]),
        ) as mock_mapper:
            await attack_service.get_conversation_messages_async(
                attack_result_id="test-id",
                conversation_id="test-id",
            )

        mock_mapper.assert_awaited_once_with([], objective_score_id=objective_score_id)

    async def test_get_conversation_messages_preserves_string_objective_score_id(
        self, attack_service, mock_memory
    ) -> None:
        """The message mapper receives string score IDs without UUID conversion."""
        ar = make_attack_result(conversation_id="test-id")
        objective_score_id = str(uuid.uuid4())
        ar.automated_score = MagicMock(id=objective_score_id)
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        with patch(
            "pyrit.backend.services.attack_service.pyrit_messages_to_dto_async",
            new=AsyncMock(return_value=[]),
        ) as mock_mapper:
            await attack_service.get_conversation_messages_async(
                attack_result_id="test-id",
                conversation_id="test-id",
            )

        mock_mapper.assert_awaited_once_with([], objective_score_id=objective_score_id)

    async def test_get_conversation_messages_raises_for_unrelated_conversation(
        self, attack_service, mock_memory
    ) -> None:
        "Test that get_conversation_messages_async raises ValueError for a conversation not belonging to the attack."
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]

        with pytest.raises(ValueError, match="not part of attack"):
            await attack_service.get_conversation_messages_async(
                attack_result_id="test-id", conversation_id="other-conv"
            )


# ============================================================================
# Create Attack Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestCreateAttack:
    """Tests for create_attack method."""

    @pytest.mark.parametrize("copy_history", [False, True])
    async def test_manual_creation_registers_ownership_async(
        self, *, sqlite_instance: SQLiteMemory, copy_history: bool
    ) -> None:
        target = MockPromptTarget()
        source_owner = str(uuid.uuid4())
        source_id = str(uuid.uuid4())
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(
                conversation_id=source_id,
                target_identifier=target.get_identifier(),
                attack_result_id=source_owner,
            )
        )
        await sqlite_instance.add_message_to_memory_async(
            request=MessagePiece(role="user", original_value="history", conversation_id=source_id).to_message()
        )
        with patch("pyrit.backend.services.attack_service.get_target_service") as targets:
            targets.return_value.get_target_async = AsyncMock(return_value=object())
            targets.return_value.get_target_object.return_value = target
            created = await AttackService().create_attack_async(
                request=CreateAttackRequest(
                    target_registry_name="target",
                    source_conversation_id=source_id if copy_history else None,
                    cutoff_index=0 if copy_history else None,
                )
            )
        [owned] = await sqlite_instance.get_attack_result_conversations_async(attack_result_id=created.attack_result_id)
        assert owned.conversation_id == created.conversation_id
        assert owned.target_identifier == target.get_identifier()
        assert owned.conversation_id != source_id
        [source] = await sqlite_instance.get_attack_result_conversations_async(attack_result_id=source_owner)
        assert source.conversation_id == source_id
        copied = await sqlite_instance.get_message_pieces_async(attack_result_id=created.attack_result_id)
        assert [piece.original_value for piece in copied] == (["history"] if copy_history else [])

    async def test_create_attack_validates_target_exists(self, attack_service) -> None:
        """Test that create_attack validates target exists."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=None)
            mock_get_target_service.return_value = mock_target_service

            with pytest.raises(ValueError, match="not found"):
                await attack_service.create_attack_async(
                    request=CreateAttackRequest(target_registry_name="nonexistent")
                )

    async def test_create_attack_stores_attack_result(self, attack_service, mock_memory) -> None:
        """Test that create_attack stores AttackResult in memory."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            result = await attack_service.create_attack_async(
                request=CreateAttackRequest(
                    target_registry_name="target-1",
                    name="My Attack",
                    operator="alice",
                    operation="nightly",
                    labels={"team": "red"},
                )
            )

            assert result.conversation_id is not None
            assert result.created_at is not None
            mock_memory.add_conversation_branches_to_attack_async.assert_called_once()
            stored_attack = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["new_attack"]
            assert stored_attack.metadata["target_registry_name"] == "target-1"
            assert stored_attack.operator == "alice"
            assert stored_attack.operation == "nightly"
            assert stored_attack.labels == {"team": "red", "source": "gui"}

    async def test_create_attack_stores_prepended_conversation(self, attack_service, mock_memory) -> None:
        """Test that create_attack stores prepended conversation messages."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            prepended = [
                PrependedMessageRequest(
                    role="system",
                    pieces=[MessagePieceRequest(original_value="You are a helpful assistant.")],
                )
            ]

            result = await attack_service.create_attack_async(
                request=CreateAttackRequest(target_registry_name="target-1", prepended_conversation=prepended)
            )

            assert result.conversation_id is not None
            mock_memory.add_conversation_branches_to_attack_async.assert_called_once()
            assert len(mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["message_pieces"]) == 1

    async def test_create_attack_rejects_prepended_media_outside_results_before_writing(
        self, attack_service, mock_memory, tmp_path: Path
    ) -> None:
        """Prepended media must point into managed storage, and nothing is written when it does not."""
        mock_memory.results_path = str(tmp_path / "results")
        outside_image = tmp_path / "outside.png"
        outside_image.write_bytes(b"PNG")
        prepended = [
            PrependedMessageRequest(
                role="user",
                pieces=[MessagePieceRequest(data_type="image_path", original_value=str(outside_image))],
            )
        ]

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service,
            pytest.raises(ValueError, match="outside the allowed results directory"),
        ):
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_get_target_service.return_value = mock_target_service
            await attack_service.create_attack_async(
                request=CreateAttackRequest(target_registry_name="target-1", prepended_conversation=prepended)
            )

        mock_memory.add_conversation_branches_to_attack_async.assert_not_called()
        mock_memory.add_attack_results_to_memory_async.assert_not_called()
        mock_memory.add_message_pieces_to_memory_async.assert_not_called()

    async def test_create_attack_persists_prepended_base64_media(self, attack_service, mock_memory) -> None:
        """Prepended base64 media is written to result storage and stored as a file path."""
        stored_path = "/results/prompt-memory-entries/images/prepended.png"
        serializer = MagicMock(value=stored_path)
        serializer.get_data_filename_async = AsyncMock(return_value=stored_path)
        serializer.save_b64_image_async = AsyncMock()
        prepended = [
            PrependedMessageRequest(
                role="user",
                pieces=[MessagePieceRequest(data_type="image_path", original_value="aW1hZ2U=", mime_type="image/png")],
            )
        ]

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service,
            patch("pyrit.backend.services.attack_service.data_serializer_factory", return_value=serializer) as factory,
            patch("pyrit.backend.services.attack_service.set_message_piece_sha256_async", new_callable=AsyncMock),
        ):
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_get_target_service.return_value = mock_target_service
            await attack_service.create_attack_async(
                request=CreateAttackRequest(target_registry_name="target-1", prepended_conversation=prepended)
            )

        assert factory.call_args.kwargs["category"] == "prompt-memory-entries"
        stored_piece = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["message_pieces"][0]
        assert stored_piece.original_value == stored_path

    async def test_create_attack_lowers_system_prompt_to_system_message(self, attack_service, mock_memory) -> None:
        """Test that system_prompt is lowered to a single system-role message at sequence 0."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            await attack_service.create_attack_async(
                request=CreateAttackRequest(target_registry_name="target-1", system_prompt="You are Bob.")
            )

            pieces = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["message_pieces"]
            assert len(pieces) == 1
            piece = pieces[0]
            assert piece.api_role == "system"
            assert piece.sequence == 0
            assert piece.original_value == "You are Bob."

    async def test_create_attack_blank_system_prompt_is_noop(self, attack_service, mock_memory) -> None:
        """Test that an empty system_prompt stores no prepended message."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            await attack_service.create_attack_async(
                request=CreateAttackRequest(target_registry_name="target-1", system_prompt="")
            )

            assert mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["message_pieces"] == []

    async def test_create_attack_system_prompt_prepends_before_prepended_conversation(
        self, attack_service, mock_memory
    ) -> None:
        """Test that system_prompt is inserted at sequence 0, ahead of prepended_conversation messages."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            prepended = [
                PrependedMessageRequest(role="user", pieces=[MessagePieceRequest(original_value="Hello")]),
            ]

            await attack_service.create_attack_async(
                request=CreateAttackRequest(
                    target_registry_name="target-1",
                    system_prompt="You are Bob.",
                    prepended_conversation=prepended,
                )
            )

            pieces = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["message_pieces"]
            assert len(pieces) == 2
            roles = [piece.api_role for piece in pieces]
            sequences = [piece.sequence for piece in pieces]
            assert roles == ["system", "user"]
            assert sequences == [0, 1]

    async def test_create_attack_does_not_store_labels_in_metadata(self, attack_service, mock_memory) -> None:
        """Test that labels are not duplicated in attack metadata."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            await attack_service.create_attack_async(
                request=CreateAttackRequest(
                    target_registry_name="target-1",
                    name="Labeled Attack",
                    labels={"env": "prod", "team": "red"},
                )
            )

            stored_ar = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["new_attack"]
            assert "labels" not in stored_ar.metadata

    async def test_create_attack_stores_labels_on_attack_result(self, attack_service, mock_memory) -> None:
        """Test that labels are stored on the attack result."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            await attack_service.create_attack_async(
                request=CreateAttackRequest(
                    target_registry_name="target-1",
                    labels={"env": "prod"},
                )
            )

            stored_ar = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["new_attack"]
            assert stored_ar.labels == {"env": "prod", "source": "gui"}

    async def test_create_attack_prepended_messages_have_incrementing_sequences(
        self, attack_service, mock_memory
    ) -> None:
        """Test that multiple prepended messages get incrementing sequence numbers and preserve lineage."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            original_id_1 = "aaaaaaaa-1111-2222-3333-444444444444"
            original_id_2 = "bbbbbbbb-1111-2222-3333-444444444444"
            original_id_3 = "cccccccc-1111-2222-3333-444444444444"

            prepended = [
                PrependedMessageRequest(
                    role="system",
                    pieces=[
                        MessagePieceRequest(
                            original_value="You are a helpful assistant.",
                            original_prompt_id=original_id_1,
                        )
                    ],
                ),
                PrependedMessageRequest(
                    role="user",
                    pieces=[
                        MessagePieceRequest(original_value="Hello", original_prompt_id=original_id_2),
                    ],
                ),
                PrependedMessageRequest(
                    role="assistant",
                    pieces=[
                        MessagePieceRequest(original_value="Hi there!", original_prompt_id=original_id_3),
                    ],
                ),
            ]

            await attack_service.create_attack_async(
                request=CreateAttackRequest(target_registry_name="target-1", prepended_conversation=prepended)
            )

            stored_pieces = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["message_pieces"]
            assert len(stored_pieces) == 3
            sequences = [piece.sequence for piece in stored_pieces]
            assert sequences == [0, 1, 2]

            roles = [piece.api_role for piece in stored_pieces]
            assert roles == ["system", "user", "assistant"]

            # original_prompt_id preserved for lineage tracking
            import uuid

            assert stored_pieces[0].original_prompt_id == uuid.UUID(original_id_1)
            assert stored_pieces[1].original_prompt_id == uuid.UUID(original_id_2)
            assert stored_pieces[2].original_prompt_id == uuid.UUID(original_id_3)

            # Each piece gets its own new id, different from the original
            for piece in stored_pieces:
                assert piece.id != piece.original_prompt_id

    async def test_create_attack_preserves_user_supplied_source_label(self, attack_service, mock_memory) -> None:
        """Test that setdefault does not overwrite user-supplied 'source' label."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            await attack_service.create_attack_async(
                request=CreateAttackRequest(
                    target_registry_name="target-1",
                    labels={"source": "api-test"},
                )
            )

            stored_ar = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["new_attack"]
            assert stored_ar.labels["source"] == "api-test"

    async def test_create_attack_default_name(self, attack_service, mock_memory) -> None:
        """Test that request.name=None uses default class_name and an empty objective."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            await attack_service.create_attack_async(request=CreateAttackRequest(target_registry_name="target-1"))

            stored_ar = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["new_attack"]
            assert stored_ar.objective == ""
            assert stored_ar.get_attack_strategy_identifier().class_name == "ManualAttack"
            assert "objective_is_placeholder" not in stored_ar.metadata

    async def test_create_attack_with_name_marks_objective_explicit(self, attack_service, mock_memory) -> None:
        """Test that a user-supplied request.name is persisted directly as the objective."""
        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service:
            mock_target_obj = MagicMock()
            mock_target_obj.get_identifier.return_value = ComponentIdentifier(
                class_name="TextTarget", class_module="pyrit.prompt_target"
            )
            mock_target_service = MagicMock()
            mock_target_service.get_target_async = AsyncMock(return_value=MagicMock(type="TextTarget"))
            mock_target_service.get_target_object.return_value = mock_target_obj
            mock_get_target_service.return_value = mock_target_service

            await attack_service.create_attack_async(
                request=CreateAttackRequest(target_registry_name="target-1", name="Extract the secret")
            )

            stored_ar = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["new_attack"]
            assert stored_ar.objective == "Extract the secret"
            assert "objective_is_placeholder" not in stored_ar.metadata


# ============================================================================
# Update Attack Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestUpdateAttack:
    """Tests for update_attack method."""

    async def test_update_attack_returns_none_for_nonexistent(self, attack_service, mock_memory) -> None:
        """Test that update_attack returns None for nonexistent attack."""
        mock_memory.get_attack_results_async.return_value = []

        result = await attack_service.update_attack_async(
            attack_result_id="nonexistent", request=UpdateAttackRequest(outcome="success")
        )

        assert result is None

    async def test_update_attack_updates_outcome_success(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'success' to AttackOutcome.SUCCESS."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="success")
        )

        mock_memory.update_attack_result_by_id_async.assert_called_once()
        call_kwargs = mock_memory.update_attack_result_by_id_async.call_args[1]
        assert call_kwargs["attack_result_id"] == "test-id"
        assert call_kwargs["update_fields"]["outcome"] == "success"

    async def test_update_attack_updates_outcome_failure(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'failure' to AttackOutcome.FAILURE."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="failure")
        )

        call_kwargs = mock_memory.update_attack_result_by_id_async.call_args[1]
        assert call_kwargs["update_fields"]["outcome"] == "failure"

    async def test_update_attack_updates_outcome_undetermined(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'undetermined' to AttackOutcome.UNDETERMINED."""
        ar = make_attack_result(conversation_id="test-id", outcome=AttackOutcome.SUCCESS)
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="undetermined")
        )

        call_kwargs = mock_memory.update_attack_result_by_id_async.call_args[1]
        assert call_kwargs["update_fields"]["outcome"] == "undetermined"

    async def test_update_attack_updates_outcome_error(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'error' to AttackOutcome.ERROR."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="error")
        )

        call_kwargs = mock_memory.update_attack_result_by_id_async.call_args[1]
        assert call_kwargs["update_fields"]["outcome"] == "error"

    async def test_update_attack_updates_objective_and_hash(self, attack_service, mock_memory) -> None:
        """Test that updating the objective keeps its lookup hash synchronized."""
        ar = make_attack_result(conversation_id="test-id", objective="")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id",
            request=UpdateAttackRequest(objective="Extract the system prompt"),
        )

        update_fields = mock_memory.update_attack_result_conditionally_async.call_args.kwargs["update_fields"]
        assert update_fields["objective"] == "Extract the system prompt"
        assert update_fields["objective_sha256"] == to_sha256("Extract the system prompt")

    async def test_update_attack_rejects_stale_objective(self, attack_service, mock_memory) -> None:
        """Test that a stale editor cannot replace a newer shared objective."""
        ar = make_attack_result(conversation_id="test-id", objective="Existing objective")
        mock_memory.get_attack_results_async.return_value = [ar]

        with pytest.raises(AttackObjectiveConflictError, match="objective changed"):
            await attack_service.update_attack_async(
                attack_result_id="test-id",
                request=UpdateAttackRequest(objective="Replacement objective", expected_objective="Older objective"),
            )

        mock_memory.update_attack_result_by_id_async.assert_not_called()

    async def test_update_attack_same_objective_is_idempotent(self, attack_service, mock_memory) -> None:
        """Test that resubmitting the existing objective does not write it again."""
        objective = "Existing objective"
        ar = make_attack_result(conversation_id="test-id", objective=objective)
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        result = await attack_service.update_attack_async(
            attack_result_id="test-id",
            request=UpdateAttackRequest(objective=objective),
        )

        assert result is not None
        assert result.objective == objective
        mock_memory.update_attack_result_by_id_async.assert_not_called()

    async def test_update_attack_bumps_timestamp(self, attack_service, mock_memory) -> None:
        """Test that update_attack bumps the timestamp recency column and does not write metadata."""
        old_time = datetime(2024, 1, 1, tzinfo=UTC)
        ar = make_attack_result(conversation_id="test-id", updated_at=old_time)
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_messages_async.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="success")
        )

        update_fields = mock_memory.update_attack_result_by_id_async.call_args[1]["update_fields"]
        assert isinstance(update_fields["timestamp"], datetime)
        assert update_fields["timestamp"] > old_time
        assert "attack_metadata" not in update_fields

    @pytest.mark.parametrize(
        ("score_value", "expected_outcome"),
        [("True", "success"), ("False", "failure")],
    )
    async def test_remove_human_score_restores_automated_outcome(
        self,
        attack_service,
        mock_memory,
        score_value: str,
        expected_outcome: str,
    ) -> None:
        """Test that removing a human score restores the automated true/false result."""
        attack = make_attack_result(conversation_id="test-id")
        attack.automated_score = Score(
            score_type="true_false",
            score_value=score_value,
            score_rationale="Automated rationale",
        )
        mock_memory.get_attack_results_async.return_value = [attack]

        await attack_service.remove_human_score_async(attack_result_id="ar-test-id")

        update_fields = mock_memory.update_attack_result_by_id_async.call_args.kwargs["update_fields"]
        assert update_fields["human_score_id"] is None
        assert update_fields["outcome"] == expected_outcome
        assert update_fields["outcome_reason"] == "Automated rationale"

    async def test_remove_human_score_without_automated_score_is_undetermined(
        self,
        attack_service,
        mock_memory,
    ) -> None:
        """Test that removing the only score makes the attack outcome undetermined."""
        attack = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [attack]

        await attack_service.remove_human_score_async(attack_result_id="ar-test-id")

        update_fields = mock_memory.update_attack_result_by_id_async.call_args.kwargs["update_fields"]
        assert update_fields["human_score_id"] is None
        assert update_fields["outcome"] == "undetermined"
        assert update_fields["outcome_reason"] is None


# ============================================================================
# Add Message Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestAddMessage:
    """Tests for add_message method."""

    @pytest.mark.parametrize("send", [False, True])
    async def test_add_message_delegates_before_reading_response(
        self, *, attack_service: AttackService, mock_memory: MagicMock, send: bool
    ) -> None:
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="branch",
            target_registry_name="target" if send else None,
            send=send,
        )
        summary = AttackSummary(conversation_id="main", attack_result_id="attack", objective="test")
        messages = ConversationMessagesResponse(conversation_id="branch", messages=[])
        with (
            patch.object(attack_service._message_send_service, "_add_message_async") as sender,
            patch.object(attack_service, "get_attack_async", return_value=summary),
            patch.object(attack_service, "get_conversation_messages_async", return_value=messages) as reader,
        ):
            result = await attack_service.add_message_async(attack_result_id="attack", request=request)

        sender.assert_awaited_once_with(attack_result_id="attack", request=request)
        reader.assert_awaited_once_with(attack_result_id="attack", conversation_id="branch")
        assert result.attack is summary
        assert result.messages is messages
        mock_memory.update_attack_result_by_id_async.assert_not_called()

    @pytest.mark.parametrize("error", [ValueError("not found"), RuntimeError("send failed")])
    async def test_add_message_propagates_service_errors(
        self, *, attack_service: AttackService, error: Exception
    ) -> None:
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")], target_conversation_id="conversation"
        )
        with (
            patch.object(attack_service._message_send_service, "_add_message_async", side_effect=error),
            patch.object(attack_service, "get_attack_async") as reader,
            pytest.raises(type(error), match=str(error)),
        ):
            await attack_service.add_message_async(attack_result_id="attack", request=request)
        reader.assert_not_called()

    @pytest.mark.timeout(10)
    @pytest.mark.parametrize("read_method", ["get_attack_async", "get_conversation_messages_async"])
    @pytest.mark.parametrize("incoming_send", [False, True])
    async def test_response_assembly_keeps_conversation_reserved_async(
        self, *, sqlite_instance: SQLiteMemory, read_method: str, incoming_send: bool
    ) -> None:
        ar = make_attack_result(conversation_id=str(uuid.uuid4()), attack_result_id=str(uuid.uuid4()), has_target=False)
        await sqlite_instance.add_attack_results_to_memory_async(attack_results=[ar])
        scheduler = ManualSendScheduler()
        sender = MessageSendService(scheduler=scheduler)
        service, peer = AttackService(message_send_service=sender), AttackService(message_send_service=sender)
        started, release = asyncio.Event(), asyncio.Event()
        read = (
            service.get_attack_async if read_method == "get_attack_async" else service.get_conversation_messages_async
        )

        async def pause_after_read_async(**kwargs: Any) -> AttackSummary | ConversationMessagesResponse | None:
            result = await read(**kwargs)
            started.set()
            await release.wait()
            return result

        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="first")],
            target_conversation_id=ar.conversation_id,
            target_registry_name="target",
        )
        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as registry,
            patch.object(service, read_method, side_effect=pause_after_read_async),
        ):
            registry.return_value.get_target_object.return_value = MockPromptTarget()
            active = asyncio.create_task(
                service.add_message_async(attack_result_id=ar.attack_result_id, request=request)
            )
            try:
                await started.wait()
                with pytest.raises(ManualSendConflictError):
                    await peer.add_message_async(
                        attack_result_id=ar.attack_result_id,
                        request=AddMessageRequest(
                            pieces=[MessagePieceRequest(original_value="next")],
                            target_conversation_id=ar.conversation_id,
                            target_registry_name="target" if incoming_send else None,
                            send=incoming_send,
                        ),
                    )
            finally:
                release.set()
                result = await active

        assert result.attack.message_count == 2
        assert [message.message_pieces[0].original_value for message in result.messages.messages] == [
            "first",
            "default",
        ]
        assert result.messages.target_response_status is not None
        assert result.messages.target_response_status.response_error == "none"
        assert not scheduler._conversations
        await peer.add_message_async(
            attack_result_id=ar.attack_result_id,
            request=AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="next")],
                target_conversation_id=ar.conversation_id,
                send=False,
            ),
        )

    @pytest.mark.timeout(10)
    @pytest.mark.parametrize("read_method", ["get_attack_async", "get_conversation_messages_async"])
    @pytest.mark.parametrize("cancel", [False, True])
    async def test_response_failure_releases_conversation_async(
        self, *, attack_service: AttackService, read_method: str, cancel: bool
    ) -> None:
        sender = attack_service._message_send_service
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")], target_conversation_id="conversation", send=False
        )
        started, release = asyncio.Event(), asyncio.Event()

        async def fail_read_async(**_: Any) -> None:
            started.set()
            await release.wait()
            raise RuntimeError("response failed")

        summary = AttackSummary(conversation_id="conversation", attack_result_id="attack", objective="test")
        with (
            patch.object(sender, "_add_message_async"),
            patch.object(attack_service, "get_attack_async", return_value=summary),
            patch.object(attack_service, read_method, side_effect=fail_read_async),
        ):
            active = asyncio.create_task(attack_service.add_message_async(attack_result_id="attack", request=request))
            try:
                await started.wait()
                assert sender._scheduler._conversations == {"conversation"}
                if cancel:
                    active.cancel()
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
                    await active
            assert not sender._scheduler._conversations
            await sender.add_message_async(attack_result_id="attack", request=request)

    @pytest.mark.parametrize(
        "role", ["system", "user", "assistant", "simulated_assistant", "tool", "simulated_tool", "developer"]
    )
    async def test_add_message_send_false_without_registry_name_succeeds(
        self, *, attack_service: AttackService, mock_memory: MagicMock, role: ChatMessageRole
    ) -> None:
        """Test that add_message with send=False does not require target_registry_name."""
        ar = make_attack_result(conversation_id="test-id")
        ar.operator = "alice"
        ar.operation = "nightly"
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = [
            MessagePiece(role="user", original_value="prior", conversation_id="test-id", sequence=3)
        ]
        mock_memory.get_conversation_messages_async.return_value = []
        target = ComponentIdentifier(class_name="TextTarget", class_module="pyrit.prompt_target")
        mock_memory.get_conversation_metadata_async.return_value = Conversation(
            conversation_id="test-id", target_identifier=target
        )
        original_id = uuid.uuid4()

        request = AddMessageRequest(
            role=role,
            pieces=[
                MessagePieceRequest(original_value="Hello", original_prompt_id=str(original_id)),
                MessagePieceRequest(original_value="World", converted_value="converted"),
            ],
            target_conversation_id="test-id",
            send=False,
        )

        with patch("pyrit.backend.services.message_send_service.PromptNormalizer") as normalizer:
            result = await attack_service.add_message_async(attack_result_id="test-id", request=request)
        normalizer.assert_not_called()
        assert result.attack is not None
        assert result.messages.conversation_id == "test-id"
        pieces = [
            piece
            for call in mock_memory.add_message_pieces_to_memory_async.call_args_list
            for piece in call.kwargs["message_pieces"]
        ]
        assert [piece.original_value for piece in pieces] == ["Hello", "World"]
        assert [piece.converted_value for piece in pieces] == ["Hello", "converted"]
        expected_role = {"assistant": "simulated_assistant", "tool": "simulated_tool"}.get(role, role)
        assert all(piece.role == expected_role and piece.sequence == 4 for piece in pieces)
        assert pieces[0].original_prompt_id == original_id
        assert mock_memory.add_conversation_to_memory_async.call_args.kwargs["conversation"].target_identifier == target

    async def test_add_message_surfaces_stored_error_piece_on_send_failure(self, attack_service, mock_memory) -> None:
        """When the normalizer stores an error piece then raises, the send returns that turn inline (no raise)."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]

        # The PromptNormalizer persists a full error piece before re-raising; model
        # that by flipping to return the stored error piece only after send fails.
        traceback_text = "Connection error.\nAPIConnectionError('Connection error.')\nTraceback..."
        request_piece = MessagePiece(
            role="user",
            original_value="Hello",
            original_value_data_type="text",
            converted_value="Hello",
            converted_value_data_type="text",
            conversation_id="test-id",
            sequence=0,
        )
        error_piece = MessagePiece(
            role="assistant",
            original_value=traceback_text,
            original_value_data_type="error",
            converted_value=traceback_text,
            converted_value_data_type="error",
            conversation_id="test-id",
            sequence=1,
            response_error="processing",
        )
        state = {"sent": False}
        mock_memory.get_message_pieces_async.side_effect = lambda **_: [error_piece] if state["sent"] else []
        # The conversation-messages read (used to build the response DTO) must include
        # the stored error turn so we can assert it is surfaced to the caller.
        mock_memory.get_conversation_messages_async.side_effect = lambda **_: (
            [Message(message_pieces=[request_piece]), Message(message_pieces=[error_piece])] if state["sent"] else []
        )

        async def _raise_after_store_async(**_: Any) -> None:
            state["sent"] = True
            raise Exception("Error sending prompt with conversation ID: test-id")

        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.message_send_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock(side_effect=_raise_after_store_async)
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="test-target",
            )

            # Should NOT raise: the stored error turn is surfaced via the normal response.
            result = await attack_service.add_message_async(attack_result_id="test-id", request=request)

            mock_normalizer.send_prompt_async.assert_called_once()
            assert result.attack is not None

            # The error turn (response_error="processing" + traceback) must come back in the response,
            # so the send-time view matches the conversation-reload view.
            returned_pieces = [piece for message in result.messages.messages for piece in message.message_pieces]
            error_views = [piece for piece in returned_pieces if piece.response_error == "processing"]
            assert len(error_views) == 1
            assert "APIConnectionError" in error_views[0].converted_value
            assert result.messages.target_response_status is not None
            assert result.messages.target_response_status.response_error == "processing"
            assert result.messages.target_response_status.request_turn_number == 0
            assert result.messages.target_response_status.response_turn_number == 1

    @pytest.mark.parametrize("failure", ["normalization", "validation", "target"])
    async def test_real_preflight_and_provider_errors_preserve_synchronous_response_async(
        self, *, sqlite_instance: SQLiteMemory, failure: str
    ) -> None:
        attack = make_attack_result(
            conversation_id=str(uuid.uuid4()), attack_result_id=str(uuid.uuid4()), has_target=False
        )
        await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
        target = MockPromptTarget()
        service = AttackService()
        method = {
            "normalization": "_get_normalized_conversation_async",
            "validation": "_validate_request",
            "target": "_send_prompt_to_target_async",
        }[failure]
        with (
            patch("pyrit.backend.services.message_send_service.get_target_service") as registry,
            patch.object(target, method, side_effect=RuntimeError("controlled failure")),
        ):
            registry.return_value.get_target_object.return_value = target
            result = await service.add_message_async(
                attack_result_id=attack.attack_result_id,
                request=AddMessageRequest(
                    pieces=[MessagePieceRequest(original_value="Hello")],
                    target_conversation_id=attack.conversation_id,
                    target_registry_name="target",
                ),
            )
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=attack.conversation_id)
        assert [piece.role for piece in pieces] == ["user", "assistant"]
        assert pieces[-1].response_error == "processing"
        assert "controlled failure" in pieces[-1].original_value
        status = result.messages.target_response_status
        assert status is not None
        assert status.response_error == "processing"
        assert (status.request_turn_number, status.response_turn_number) == (0, 1)
        reloaded = await service.get_conversation_messages_async(
            attack_result_id=attack.attack_result_id, conversation_id=attack.conversation_id
        )
        assert reloaded is not None
        assert reloaded.target_response_status == status
        assert reloaded.messages == result.messages.messages

    async def test_add_message_raises_when_attack_not_found_after_update(self, attack_service, mock_memory) -> None:
        """Test that add_message raises ValueError when attack disappears after update."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []
        mock_memory.get_conversation_messages_async.return_value = []

        request = AddMessageRequest(
            role="system",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        with patch.object(attack_service, "get_attack_async", new=AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="not found after update"):
                await attack_service.add_message_async(attack_result_id="test-id", request=request)

    async def test_add_message_raises_when_messages_not_found_after_update(self, attack_service, mock_memory) -> None:
        """Test that add_message raises ValueError when messages disappear after update."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []
        mock_memory.get_conversation_messages_async.return_value = []

        request = AddMessageRequest(
            role="system",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        with (
            patch.object(attack_service, "get_attack_async", new=AsyncMock(return_value=MagicMock())),
            patch.object(attack_service, "get_conversation_messages_async", new=AsyncMock(return_value=None)),
        ):
            with pytest.raises(ValueError, match="messages not found after update"):
                await attack_service.add_message_async(attack_result_id="test-id", request=request)


# ============================================================================
# Pagination Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestPagination:
    """Tests for pagination in list_attacks."""

    @pytest.mark.parametrize(
        "name",
        [
            "DecodedKeysetCursor",
            "decode_keyset_cursor",
            "encode_keyset_cursor",
            "fingerprint_filters",
            "normalize_label_filters",
        ],
    )
    def test_pagination_reexports_preserve_identity(self, name: str) -> None:
        assert getattr(backend_pagination, name) is getattr(common_pagination, name)

    async def test_list_attacks_first_page_forwards_limit_plus_one_and_no_after(
        self, attack_service, mock_memory
    ) -> None:
        """The first page over-fetches one row (limit + 1) and passes no keyset anchor."""
        mock_memory.get_attack_results_async.return_value = []

        await attack_service.list_attacks_async(limit=20)

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["limit"] == 21
        assert call_kwargs["after"] is None

    async def test_list_attacks_empty_attack_types_match_no_filter_cursor(self, attack_service, mock_memory) -> None:
        """An empty attack-type list has the same query and cursor fingerprint as no filter."""
        backing = _paginated_backing(3)
        mock_memory.get_attack_results_async.side_effect = _keyset_side_effect(backing)

        first = await attack_service.list_attacks_async(limit=2)
        assert first.pagination.next_cursor is not None
        await attack_service.list_attacks_async(
            attack_types=[],
            limit=2,
            cursor=first.pagination.next_cursor,
        )

        call_kwargs = mock_memory.get_attack_results_async.call_args.kwargs
        assert call_kwargs["attack_classes"] is None
        assert call_kwargs["after"].attack_result_id == backing[1].attack_result_id

    async def test_list_attacks_decodes_cursor_to_after(self, attack_service, mock_memory) -> None:
        """A cursor is decoded into the memory keyset anchor when its filter fingerprint matches."""
        mock_memory.get_attack_results_async.return_value = []
        anchor_row = make_attack_result(conversation_id="attack-anchor", attack_result_id=str(uuid.uuid4()))

        await attack_service.list_attacks_async(limit=20, cursor=_cursor_for(anchor_row))

        call_kwargs = mock_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["after"].attack_result_id == anchor_row.attack_result_id
        assert call_kwargs["limit"] == 21

    async def test_list_attacks_invalid_cursor_defaults_to_first_page(self, attack_service, mock_memory) -> None:
        """A malformed or legacy (offset/attack-result-id) cursor degrades to the first page."""
        mock_memory.get_attack_results_async.return_value = []

        await attack_service.list_attacks_async(limit=20, cursor="ar-attack-1")

        assert mock_memory.get_attack_results_async.call_args[1]["after"] is None

    @pytest.mark.parametrize("field", ["t", "i"])
    async def test_list_attacks_non_string_cursor_fields_restart_async(
        self, *, attack_service: AttackService, mock_memory: MagicMock, field: str
    ) -> None:
        payload = {
            "v": 1,
            "f": _attack_filter_fingerprint(),
            "t": "2026-09-21T12:00:00Z",
            "i": "00000000-0000-0000-0000-000000000001",
            field: 123,
        }
        cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        mock_memory.get_attack_results_async.return_value = []

        await attack_service.list_attacks_async(limit=20, cursor=cursor)

        assert mock_memory.get_attack_results_async.call_args.kwargs["after"] is None

    def test_decode_attack_cursor_rejects_invalid_and_round_trips_valid(self) -> None:
        """Bad/legacy/mismatched/naive cursors decode to None; valid round-trips; non-UTC canonicalizes to UTC."""
        fingerprint = _attack_filter_fingerprint()

        def decode(cursor, fp=fingerprint):
            return decode_keyset_cursor(cursor=cursor, fingerprint=fp)

        assert decode(None) is None
        assert decode("") is None
        assert decode("not-base64!!!") is None
        assert decode("ar-attack-1") is None  # legacy attack-result-id cursor
        assert decode("deadbeef.40") is None  # legacy offset cursor

        anchor = make_attack_result(conversation_id="attack-1", attack_result_id=str(uuid.uuid4()))
        valid = _cursor_for(anchor, fingerprint=fingerprint)
        # A cursor minted for a different filter set is rejected.
        assert decode(valid, "0000000000000000") is None
        decoded = decode(valid)
        assert decoded is not None
        assert decoded.identifier == anchor.attack_result_id
        assert decoded.timestamp == anchor.timestamp

        # A crafted cursor carrying a naive (tz-less) timestamp is rejected: service-minted anchors
        # are always timezone-aware, and a naive anchor would bind inconsistently in the seek.
        naive_payload = {
            "f": fingerprint,
            "t": "2026-01-01T00:00:00",
            "i": str(uuid.uuid4()),
        }
        naive_cursor = base64.urlsafe_b64encode(json.dumps(naive_payload).encode("utf-8")).decode("ascii").rstrip("=")
        assert decode(naive_cursor) is None

        # A crafted cursor carrying a non-UTC aware offset is canonicalized to UTC so its seek
        # tie-break matches the UTC-normalized timestamp column (service cursors are already UTC).
        offset_payload = {
            "f": fingerprint,
            "t": "2026-01-01T00:00:00+05:00",
            "i": str(uuid.uuid4()),
        }
        offset_cursor = base64.urlsafe_b64encode(json.dumps(offset_payload).encode("utf-8")).decode("ascii").rstrip("=")
        offset_decoded = decode(offset_cursor)
        assert offset_decoded is not None
        assert offset_decoded.timestamp == datetime(2025, 12, 31, 19, 0, 0, tzinfo=UTC)
        assert offset_decoded.timestamp.tzinfo == UTC

        # A crafted cursor whose extreme UTC offset would push the timestamp past datetime's
        # representable range when normalized to UTC decodes to None instead of raising.
        overflow_payload = {
            "f": fingerprint,
            "t": "0001-01-01T00:00:00+23:59",
            "i": str(uuid.uuid4()),
        }
        overflow_cursor = (
            base64.urlsafe_b64encode(json.dumps(overflow_payload).encode("utf-8")).decode("ascii").rstrip("=")
        )
        assert decode(overflow_cursor) is None

    async def test_list_attacks_has_more_and_next_cursor(self, attack_service, mock_memory) -> None:
        """When an extra row is returned, has_more is set and next_cursor anchors on the last row."""
        backing = _paginated_backing(5)
        mock_memory.get_attack_results_async.side_effect = _keyset_side_effect(backing)

        result = await attack_service.list_attacks_async(limit=2)

        assert len(result.items) == 2
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor == _cursor_for(backing[1])

    async def test_list_attacks_second_page_via_cursor_is_disjoint(self, attack_service, mock_memory) -> None:
        """Following next_cursor returns the next disjoint page."""
        backing = _paginated_backing(5)
        mock_memory.get_attack_results_async.side_effect = _keyset_side_effect(backing)

        result = await attack_service.list_attacks_async(limit=2, cursor=_cursor_for(backing[1]))

        assert [item.conversation_id for item in result.items] == ["attack-2", "attack-3"]
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor == _cursor_for(backing[3])

    async def test_list_attacks_last_page_has_no_next_cursor(self, attack_service, mock_memory) -> None:
        """The final page reports has_more False and a null next_cursor."""
        backing = _paginated_backing(5)
        mock_memory.get_attack_results_async.side_effect = _keyset_side_effect(backing)

        result = await attack_service.list_attacks_async(limit=2, cursor=_cursor_for(backing[3]))

        assert [item.conversation_id for item in result.items] == ["attack-4"]
        assert result.pagination.has_more is False
        assert result.pagination.next_cursor is None

    async def test_list_attacks_prev_cursor_echoes_incoming_cursor(self, attack_service, mock_memory) -> None:
        """prev_cursor echoes the incoming cursor unchanged."""
        mock_memory.get_attack_results_async.return_value = []
        cursor = _cursor_for(make_attack_result(conversation_id="attack-1", attack_result_id=str(uuid.uuid4())))

        result = await attack_service.list_attacks_async(limit=2, cursor=cursor)

        assert result.pagination.prev_cursor == cursor

    async def test_list_attacks_stale_cursor_after_filter_change_resets_to_first_page(
        self, attack_service, mock_memory
    ) -> None:
        """A cursor minted for one filter set falls back to page 1 when the filters change.

        This is the core cursor-fingerprint guarantee: without the filter fingerprint, replaying a
        keyset anchor against a different result set would seek within the wrong data set. With it,
        the stale cursor degrades to the first page of the new filter set, matching the
        pre-optimization id-cursor behavior.
        """
        backing = _paginated_backing(5)
        mock_memory.get_attack_results_async.side_effect = _keyset_side_effect(backing)

        # Page 1 with no filter yields a next_cursor anchored on the last row.
        first = await attack_service.list_attacks_async(limit=2)
        stale_cursor = first.pagination.next_cursor
        assert stale_cursor is not None

        # Replaying it with a different filter set must reset to the first page, not seek.
        result = await attack_service.list_attacks_async(limit=2, cursor=stale_cursor, outcome="success")

        assert mock_memory.get_attack_results_async.call_args[1]["after"] is None
        assert [item.conversation_id for item in result.items] == ["attack-0", "attack-1"]

    async def test_list_attacks_cursor_with_matching_filters_preserves_anchor(
        self, attack_service, mock_memory
    ) -> None:
        """A cursor replayed with the same filter set applies its encoded keyset anchor."""
        backing = _paginated_backing(5)
        mock_memory.get_attack_results_async.side_effect = _keyset_side_effect(backing)

        first = await attack_service.list_attacks_async(limit=2, outcome="success")
        next_cursor = first.pagination.next_cursor
        assert next_cursor is not None

        result = await attack_service.list_attacks_async(limit=2, cursor=next_cursor, outcome="success")

        assert (
            mock_memory.get_attack_results_async.call_args[1]["after"].attack_result_id == backing[1].attack_result_id
        )
        assert [item.conversation_id for item in result.items] == ["attack-2", "attack-3"]

    def test_attack_filter_fingerprint_is_order_independent_and_filter_sensitive(self) -> None:
        """The fingerprint normalizes ordering but distinguishes different filter values."""
        fingerprint = _attack_filter_fingerprint
        assert fingerprint(attack_types=["a", "b"]) == fingerprint(attack_types=["b", "a"])
        assert fingerprint(labels={"op": ["red", "blue"]}) == fingerprint(labels={"op": ["blue", "red"]})
        assert fingerprint() != fingerprint(outcome="success")
        assert fingerprint(outcome="success") != fingerprint(outcome="failure")
        assert fingerprint() != fingerprint(include_scenario_attacks=False)
        assert fingerprint(min_turns=1) != fingerprint(max_turns=1)
        # An empty-sequence label is a no-op filter in get_attack_results (effective_labels),
        # so it must fingerprint identically to no label filter — otherwise a cursor minted
        # with it would spuriously reset pagination to the first page.
        assert fingerprint(labels={"op": []}) == fingerprint()
        assert fingerprint(labels={"op": []}) == fingerprint(labels=None)
        assert fingerprint(labels={"op": [], "team": "red"}) == fingerprint(labels={"team": "red"})

    async def test_list_attacks_uses_conversation_stats_not_pieces(self, attack_service, mock_memory) -> None:
        "Test that list_attacks uses get_conversation_stats_async instead of loading full pieces."
        backing = _paginated_backing(5)
        mock_memory.get_attack_results_async.side_effect = _keyset_side_effect(backing)

        await attack_service.list_attacks_async(limit=2)

        # get_conversation_stats should be called once (batched), not per-attack
        mock_memory.get_conversation_stats_async.assert_called_once()
        # get_message_pieces should NOT be called by list_attacks
        mock_memory.get_message_pieces_async.assert_not_called()


# ============================================================================
# Message Building Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestMessageBuilding:
    """Tests for message translation and building."""

    async def test_get_attack_with_messages_translates_correctly(self, attack_service, mock_memory) -> None:
        "Test that get_conversation_messages_async translates PyRIT messages to backend format."
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results_async.return_value = [ar]

        piece = MessagePiece(
            role="user",
            original_value="Hello",
            converted_value="Hello",
            original_value_data_type="text",
            converted_value_data_type="text",
            conversation_id="test-id",
            sequence=0,
        )
        msg = Message(message_pieces=[piece])

        mock_memory.get_conversation_messages_async.return_value = [msg]

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="test-id", conversation_id="test-id"
        )

        assert result is not None
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"
        assert len(result.messages[0].message_pieces) == 1
        assert result.messages[0].message_pieces[0].original_value == "Hello"


# ============================================================================
# Singleton Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestAttackServiceSingleton:
    """Tests for get_attack_service singleton function."""

    def test_get_attack_service_returns_attack_service(self) -> None:
        """Test that get_attack_service returns an AttackService instance."""
        get_attack_service.cache_clear()

        with patch.object(CentralMemory, "get_memory_instance"):
            service = get_attack_service()
            assert isinstance(service, AttackService)

    def test_get_attack_service_returns_same_instance(self) -> None:
        """Test that get_attack_service returns the same instance."""
        get_attack_service.cache_clear()

        with patch.object(CentralMemory, "get_memory_instance"):
            service1 = get_attack_service()
            service2 = get_attack_service()
            assert service1 is service2


# ============================================================================
# Persist Base64 Pieces Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestPersistBase64Pieces:
    """Tests for _persist_base64_pieces_async helper."""

    @pytest.mark.parametrize(
        ("original_type", "original_value", "converted_type", "converted_value", "expected_types", "extensions"),
        [
            (
                "text",
                "source",
                "image_path",
                "data:image/png;base64,cHJldmlldw==",
                ["image_path"],
                [".png"],
            ),
            (
                "image_path",
                "data:image/png;base64,c291cmNl",
                "text",
                "Exact description",
                ["image_path"],
                [".png"],
            ),
            (
                "image_path",
                "data:image/png;base64,c291cmNl",
                "audio_path",
                "data:audio/wav;base64,cHJldmlldw==",
                ["image_path", "audio_path"],
                [".png", ".wav"],
            ),
            (
                "image_path",
                "data:image/png;base64,c291cmNl",
                "image_path",
                "data:image/jpeg;base64,cHJldmlldw==",
                ["image_path", "image_path"],
                [".png", ".jpg"],
            ),
        ],
    )
    async def test_persists_original_and_converted_media_independently_async(
        self,
        *,
        original_type: PromptDataType,
        original_value: str,
        converted_type: PromptDataType,
        converted_value: str,
        expected_types: list[PromptDataType],
        extensions: list[str],
    ) -> None:
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    data_type=original_type,
                    original_value=original_value,
                    converted_value=converted_value,
                    converted_value_data_type=converted_type,
                    mime_type="image/png" if original_type == "image_path" else "text/plain",
                )
            ],
            send=False,
            target_conversation_id="test-id",
        )
        serializers = [MagicMock(value=f"saved-{index}{extension}") for index, extension in enumerate(extensions)]
        for serializer in serializers:
            serializer.save_b64_image_async = AsyncMock()

        with patch("pyrit.backend.services.attack_service.data_serializer_factory", side_effect=serializers) as factory:
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        assert [call.kwargs["data_type"] for call in factory.call_args_list] == expected_types
        assert [call.kwargs["extension"] for call in factory.call_args_list] == extensions
        piece = request.pieces[0]
        assert piece.data_type == original_type
        assert piece.converted_value_data_type == converted_type
        assert piece.original_value == (serializers[0].value if original_type == "image_path" else original_value)
        assert piece.converted_value == (converted_value if converted_type == "text" else serializers[-1].value)
        for serializer in serializers:
            serializer.save_b64_image_async.assert_awaited_once()

    @pytest.mark.parametrize("reference", ["media_reference", "stored_file", "results_blob"])
    async def test_converted_media_references_are_not_repersisted_async(
        self, *, attack_service, mock_memory: MagicMock, tmp_path: Path, reference: str
    ) -> None:
        stored_path = (tmp_path / "prompt-memory-entries" / "preview.png").resolve()
        mock_memory.results_path = str(tmp_path)
        if reference == "media_reference":
            converted_value, expected_value = f"/api/media?path={stored_path}", str(stored_path)
        elif reference == "stored_file":
            stored_path.parent.mkdir(parents=True)
            stored_path.write_bytes(b"PNG")
            converted_value, expected_value = str(stored_path), str(stored_path)
        else:
            mock_memory.results_path = "https://account.blob.core.windows.net/results"
            expected_value = f"{mock_memory.results_path}/prompt-memory-entries/preview.png"
            converted_value = f"{expected_value}?sv=1"
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    original_value="source",
                    converted_value=converted_value,
                    converted_value_data_type="image_path",
                )
            ],
            send=False,
            target_conversation_id="test-id",
        )
        with patch("pyrit.backend.services.attack_service.data_serializer_factory") as factory:
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        assert request.pieces[0].original_value == "source"
        assert request.pieces[0].converted_value == expected_value
        factory.assert_not_called()

    async def test_identical_original_and_converted_media_saved_once_async(self) -> None:
        request = AddMessageRequest(
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="data:image/png;base64,c291cmNl",
                    converted_value="data:image/png;base64,c291cmNl",
                )
            ],
            send=False,
            target_conversation_id="test-id",
        )
        serializer = MagicMock(value="saved.png")
        serializer.save_b64_image_async = AsyncMock()
        with patch("pyrit.backend.services.attack_service.data_serializer_factory", return_value=serializer):
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        serializer.save_b64_image_async.assert_awaited_once()
        assert request.pieces[0].original_value == "saved.png"
        assert request.pieces[0].converted_value == "saved.png"

    async def test_converted_media_failure_preserves_both_request_values_async(self) -> None:
        piece = MessagePieceRequest(
            data_type="image_path",
            original_value="data:image/png;base64,c291cmNl",
            converted_value="data:image/png;base64,cHJldmlldw==",
        )
        request = AddMessageRequest(pieces=[piece], send=False, target_conversation_id="test-id")
        before = piece.model_dump()
        original_serializer = MagicMock(value="source.png")
        original_serializer.save_b64_image_async = AsyncMock()
        converted_serializer = MagicMock()
        converted_serializer.save_b64_image_async = AsyncMock(side_effect=OSError("preview save failed"))
        with (
            patch(
                "pyrit.backend.services.attack_service.data_serializer_factory",
                side_effect=[original_serializer, converted_serializer],
            ),
            pytest.raises(OSError, match="preview save failed"),
        ):
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        assert piece.model_dump() == before

    async def test_text_pieces_are_unchanged(self, attack_service) -> None:
        """Text pieces should not be modified."""
        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="text", original_value="hello")],
            send=False,
            target_conversation_id="test-id",
        )
        await AttackService._persist_base64_pieces_async(pieces=request.pieces)
        assert request.pieces[0].original_value == "hello"

    async def test_image_piece_is_saved_to_file(self, attack_service) -> None:
        """Base64 image data should be saved to disk and value replaced with file path."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="aW1hZ2VkYXRh",  # base64 for "imagedata"
                    mime_type="image/png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/image.png"

        with patch(
            "pyrit.backend.services.attack_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as factory_mock:
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="image_path",
            extension=".png",
        )
        mock_serializer.save_b64_image_async.assert_awaited_once_with(data="aW1hZ2VkYXRh")
        assert request.pieces[0].original_value == "/saved/image.png"

    async def test_mixed_pieces_only_persists_non_text(self, attack_service) -> None:
        """Only non-text pieces should be persisted; text pieces stay untouched."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(data_type="text", original_value="describe this"),
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="base64data",
                    mime_type="image/jpeg",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/photo.jpg"

        with patch(
            "pyrit.backend.services.attack_service.data_serializer_factory",
            return_value=mock_serializer,
        ):
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        assert request.pieces[0].original_value == "describe this"
        assert request.pieces[1].original_value == "/saved/photo.jpg"

    async def test_unknown_mime_type_uses_bin_extension(self, attack_service) -> None:
        """When mime_type is missing, .bin should be used as fallback extension."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="binary_path",
                    original_value="base64data",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/file.bin"

        with patch(
            "pyrit.backend.services.attack_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as factory_mock:
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="binary_path",
            extension=".bin",
        )

    async def test_data_uri_prefix_is_stripped_before_saving(self, attack_service) -> None:
        """Data URIs (data:<mime>;base64,...) should be stripped to raw base64 before saving."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="data:image/png;base64,aW1hZ2VkYXRh",
                    mime_type="image/png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/image.png"

        with patch(
            "pyrit.backend.services.attack_service.data_serializer_factory",
            return_value=mock_serializer,
        ):
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        # Should receive only the base64 payload, not the data URI prefix
        mock_serializer.save_b64_image_async.assert_awaited_once_with(data="aW1hZ2VkYXRh")
        assert request.pieces[0].original_value == "/saved/image.png"

    async def test_data_uri_mime_type_supplies_extension_when_mime_type_missing(self, attack_service) -> None:
        """Data URI media type should prevent image uploads from falling back to blocked .bin files."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="data:image/png;base64,aW1hZ2VkYXRh",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/image.png"

        with patch(
            "pyrit.backend.services.attack_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as factory_mock:
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="image_path",
            extension=".png",
        )
        mock_serializer.save_b64_image_async.assert_awaited_once_with(data="aW1hZ2VkYXRh")
        assert request.pieces[0].original_value == "/saved/image.png"

    async def test_path_data_type_supplies_extension_when_mime_type_missing(self, attack_service) -> None:
        """Raw image base64 without MIME metadata should still use a media-serving extension."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="aW1hZ2VkYXRh",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock()
        mock_serializer.value = "/saved/image.png"

        with patch(
            "pyrit.backend.services.attack_service.data_serializer_factory",
            return_value=mock_serializer,
        ) as factory_mock:
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="image_path",
            extension=".png",
        )
        assert request.pieces[0].original_value == "/saved/image.png"

    async def test_http_url_is_kept_as_is(self, attack_service, mock_memory: MagicMock) -> None:
        """HTTPS blob URLs inside the results container are kept as references, without their query string."""
        mock_memory.results_path = "https://myblob.blob.core.windows.net/results"
        blob_url = "https://myblob.blob.core.windows.net/results/prompt-memory-entries/images/photo.png"
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value=f"{blob_url}?sv=2024",
                    mime_type="image/png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        assert request.pieces[0].original_value == blob_url
        assert request.pieces[0].converted_value == request.pieces[0].original_value

    async def test_media_reference_is_resolved_without_persistence(
        self, attack_service, mock_memory: MagicMock, tmp_path: Path
    ) -> None:
        """Local media URLs are converted back to their canonical file paths."""
        mock_memory.results_path = str(tmp_path)
        stored_path = (tmp_path / "prompt-memory-entries" / "image.png").resolve()
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value=f"/api/media?path={quote(str(stored_path), safe='')}",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        with patch("pyrit.backend.services.attack_service.data_serializer_factory") as factory:
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        assert request.pieces[0].original_value == str(stored_path)
        assert request.pieces[0].converted_value == str(stored_path)
        factory.assert_not_called()

    async def test_existing_file_is_kept_without_persistence(
        self, attack_service, mock_memory: MagicMock, tmp_path: Path
    ) -> None:
        """An existing stored file remains the canonical original and converted value."""
        mock_memory.results_path = str(tmp_path)
        media_path = (tmp_path / "prompt-memory-entries" / "images" / "image.png").resolve()
        media_path.parent.mkdir(parents=True)
        media_path.write_bytes(b"image")
        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="image_path", original_value=str(media_path))],
            send=False,
            target_conversation_id="test-id",
        )

        with patch("pyrit.backend.services.attack_service.data_serializer_factory") as factory:
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        assert request.pieces[0].original_value == str(media_path)
        assert request.pieces[0].converted_value == str(media_path)
        factory.assert_not_called()

    async def test_non_path_data_types_are_skipped(self, attack_service) -> None:
        """Non *_path types like reasoning, url, function_call should not be decoded."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(data_type="reasoning", original_value="thinking step"),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        assert request.pieces[0].original_value == "thinking step"

    async def test_long_base64_audio_does_not_crash(self, attack_service) -> None:
        """Base64 audio data longer than OS path limits should be saved, not crash with OSError."""
        # Simulate a base64-encoded WAV file (>4096 chars, exceeds Linux filename limit of 255)
        long_b64 = "UklGRiQ" + "A" * 5000  # fake WAV header + padding
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="audio_path",
                    original_value=long_b64,
                    mime_type="audio/wav",
                )
            ],
            send=False,
            target_conversation_id="test-id",
        )

        with patch("pyrit.backend.services.attack_service.data_serializer_factory") as mock_factory:
            mock_serializer = AsyncMock()
            mock_serializer.value = "/tmp/saved_audio.wav"
            mock_factory.return_value = mock_serializer

            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

            mock_factory.assert_called_once()
            mock_serializer.save_b64_image_async.assert_called_once_with(data=long_b64)
            assert request.pieces[0].original_value == "/tmp/saved_audio.wav"

    async def test_persistence_failure_does_not_partially_mutate_piece(self, attack_service) -> None:
        """A failed save leaves both request values unchanged."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="aW1hZ2VkYXRh",
                    mime_type="image/png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )
        mock_serializer = MagicMock()
        mock_serializer.save_b64_image_async = AsyncMock(side_effect=OSError("save failed"))

        with (
            patch(
                "pyrit.backend.services.attack_service.data_serializer_factory",
                return_value=mock_serializer,
            ),
            pytest.raises(OSError, match="save failed"),
        ):
            await AttackService._persist_base64_pieces_async(pieces=request.pieces)

        assert request.pieces[0].original_value == "aW1hZ2VkYXRh"
        assert request.pieces[0].converted_value is None


# ============================================================================
# Related Conversations Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestGetConversations:
    """Tests for get_conversations_async."""

    async def test_returns_none_when_attack_not_found(self, attack_service, mock_memory):
        """Should return None when the attack doesn't exist."""
        mock_memory.get_attack_results_async.return_value = []

        result = await attack_service.get_conversations_async(attack_result_id="missing")

        assert result is None

    async def test_returns_main_conversation_only(self, attack_service, mock_memory):
        """Should return only the main conversation when no related conversations exist."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_stats_async.return_value = {
            "attack-1": ConversationStats(message_count=2, last_message_preview="test"),
        }

        result = await attack_service.get_conversations_async(attack_result_id="attack-1")

        assert result is not None
        assert result.main_conversation_id == "attack-1"
        assert len(result.conversations) == 1
        assert result.conversations[0].message_count == 2

    async def test_conversation_summary_formats_media_preview(self, attack_service, mock_memory):
        """ConversationSummary previews must not leak absolute media paths."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]
        path = r"C:\Users\someone\PyRIT\dbdata\prompt-memory-entries\audio\1780010098266691.mp3"
        mock_memory.get_conversation_stats_async.return_value = {
            "attack-1": ConversationStats(
                message_count=1,
                last_message_preview=path,
                last_message_data_type="audio_path",
            ),
        }

        result = await attack_service.get_conversations_async(attack_result_id="attack-1")

        assert result is not None
        preview = result.conversations[0].last_message_preview
        assert preview == "[Audio: 1780010098266691.mp3]"
        assert "C:\\" not in (preview or "")

    async def test_returns_main_and_related_conversations(self, attack_service, mock_memory):
        """Should return main and PRUNED conversations sorted by timestamp."""
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations.add(
            ConversationReference(
                conversation_id="branch-1",
                conversation_type=ConversationType.PRUNED,
                description="Branch 1",
            )
        )
        ar.related_conversations.add(
            ConversationReference(
                conversation_id="score-1",
                conversation_type=ConversationType.SCORE,
                description="Scoring conversation",
            )
        )
        ar.related_conversations.add(
            ConversationReference(
                conversation_id="preparation-1",
                conversation_type=ConversationType.PREPARATION,
                description="Preparation conversation",
            )
        )

        mock_memory.get_attack_results_async.return_value = [ar]

        t1 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 1, 1, 9, 30, 0, tzinfo=UTC)  # earlier than t1

        mock_memory.get_conversation_stats_async.return_value = {
            "attack-1": ConversationStats(message_count=1, last_message_preview="test", created_at=t1),
            "branch-1": ConversationStats(message_count=2, last_message_preview="test", created_at=t2),
            "score-1": ConversationStats(message_count=0),
            "preparation-1": ConversationStats(message_count=2),
        }

        result = await attack_service.get_conversations_async(attack_result_id="attack-1")

        assert result is not None
        assert result.main_conversation_id == "attack-1"
        assert len(result.conversations) == 2

        main_conv = next(c for c in result.conversations if c.conversation_id == "attack-1")
        assert main_conv.message_count == 1
        assert main_conv.created_at is not None

        branch = next(c for c in result.conversations if c.conversation_id == "branch-1")
        assert branch.message_count == 2

        # Conversations should be sorted by created_at (branch-1 is earliest)
        assert result.conversations[0].conversation_id == "branch-1"
        assert result.conversations[1].conversation_id == "attack-1"


@pytest.mark.usefixtures("patch_central_database")
class TestCreateRelatedConversation:
    """Tests for create_related_conversation_async."""

    async def test_empty_history_branch_preserves_target_on_first_send_async(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        service = AttackService()
        target = MockPromptTarget()
        target_identifier = target.get_identifier()
        attack = AttackResult(
            conversation_id=str(uuid.uuid4()),
            objective="Branch an empty history",
            atomic_attack_identifier=AtomicAttackIdentifier.build(
                attack_identifier=AttackIdentifier(
                    class_name="ManualAttack",
                    class_module="pyrit.backend",
                    objective_target=target_identifier,
                )
            ),
        )
        await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
        assert await sqlite_instance.get_conversation_metadata_async(conversation_id=attack.conversation_id) is None

        branch = await service.create_related_conversation_async(
            attack_result_id=attack.attack_result_id,
            request=CreateConversationRequest(source_conversation_id=attack.conversation_id, cutoff_index=0),
        )

        assert branch is not None
        normalizer = PromptNormalizer()
        for conversation_id in [attack.conversation_id, branch.conversation_id]:
            await normalizer.send_prompt_async(
                message=Message.from_prompt(prompt="Hello", role="user"),
                target=target,
                conversation_id=conversation_id,
            )
            metadata = await sqlite_instance.get_conversation_metadata_async(conversation_id=conversation_id)
            assert metadata is not None
            assert metadata.target_identifier == target_identifier
            assert metadata.attack_result_id == attack.attack_result_id
            async with await sqlite_instance.get_session_async() as session:
                rows = (
                    await session.scalars(
                        select(ConversationEntry).where(ConversationEntry.conversation_id == conversation_id)
                    )
                ).all()
            assert rows[0].target_identifier_hash == target_identifier.hash
        assert target.prompt_sent == ["Hello", "Hello"]
        owned = await sqlite_instance.get_attack_result_conversations_async(attack_result_id=attack.attack_result_id)
        assert {conversation.conversation_id for conversation in owned} == {
            attack.conversation_id,
            branch.conversation_id,
        }

    async def test_nested_branching_preserves_target_after_version_change_async(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        service = AttackService()
        target = TargetIdentifier(class_name="ExampleTarget", class_module="tests.unit", pyrit_version="1.1.0")
        source = Conversation(conversation_id=str(uuid.uuid4()), target_identifier=target)
        attack = AttackResult(conversation_id=source.conversation_id, objective="Branch older history")
        await sqlite_instance.add_attack_results_to_memory_async(attack_results=[attack])
        with patch.object(pyrit, "__version__", "1.1.0"):
            await sqlite_instance.add_conversation_to_memory_async(conversation=source)
        original = MessagePiece(
            conversation_id=source.conversation_id, role="user", original_value="Original", sequence=0
        )
        await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[original])
        expected_rows = [(source.conversation_id, "1.1.0", target.model_dump())]

        source_id = source.conversation_id
        for _ in range(2):
            branch = await service.create_related_conversation_async(
                attack_result_id=attack.attack_result_id,
                request=CreateConversationRequest(source_conversation_id=source_id, cutoff_index=0),
            )
            assert branch is not None
            async with await sqlite_instance.get_session_async() as session:
                rows = (
                    await session.scalars(
                        select(ConversationEntry).where(ConversationEntry.conversation_id == branch.conversation_id)
                    )
                ).all()
            expected_rows.append((branch.conversation_id, rows[0].pyrit_version, rows[0].target_identifier))
            pieces = await sqlite_instance.get_message_pieces_async(conversation_id=branch.conversation_id)
            assert [piece.original_prompt_id for piece in pieces] == [original.id]
            source_id = branch.conversation_id

        async with await sqlite_instance.get_session_async() as session:
            rows = (await session.scalars(select(ConversationEntry))).all()
        assert {row.conversation_id: (row.pyrit_version, row.target_identifier) for row in rows} == {
            conversation_id: (version, identifier) for conversation_id, version, identifier in expected_rows
        }
        assert all(row.target_identifier_hash == target.hash for row in rows)
        assert all(str(row.attack_result_id) == attack.attack_result_id for row in rows)
        current = await sqlite_instance.get_attack_results_async(attack_result_ids=[attack.attack_result_id])
        assert current[0].get_active_conversation_ids() == {row.conversation_id for row in rows}

    async def test_returns_none_when_attack_not_found(self, attack_service, mock_memory):
        """Should return None when the attack doesn't exist."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        mock_memory.get_attack_results_async.return_value = []

        result = await attack_service.create_related_conversation_async(
            attack_result_id="missing",
            request=CreateConversationRequest(),
        )

        assert result is None

    async def test_creates_conversation_and_adds_to_related(self, attack_service, mock_memory):
        """Should create a new conversation and add it to pruned_conversation_ids."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []

        request = CreateConversationRequest()

        result = await attack_service.create_related_conversation_async(
            attack_result_id="attack-1",
            request=request,
        )

        assert result is not None
        assert result.conversation_id is not None
        assert result.conversation_id != "attack-1"

        mock_memory.add_conversation_branches_to_attack_async.assert_called_once()
        call_kwargs = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs
        assert call_kwargs["attack_result_id"] == "attack-1"
        assert [conversation.conversation_id for conversation in call_kwargs["conversations"]] == [
            result.conversation_id
        ]
        attack_identifier = ar.get_attack_strategy_identifier()
        assert attack_identifier is not None
        assert call_kwargs["conversations"][0].target_identifier == attack_identifier.get_child("objective_target")
        assert call_kwargs["message_pieces"] == []
        assert call_kwargs["source_conversation"] is None
        mock_memory.update_attack_result_by_id_async.assert_not_called()

    async def test_returns_none_when_attack_disappears_during_creation_async(
        self, *, attack_service: AttackService, mock_memory: MagicMock
    ) -> None:
        mock_memory.get_attack_results_async.return_value = [make_attack_result()]
        mock_memory.add_conversation_branches_to_attack_async.return_value = False

        result = await attack_service.create_related_conversation_async(
            attack_result_id="ar-attack-1", request=CreateConversationRequest()
        )

        assert result is None
        mock_memory.add_conversation_to_memory_async.assert_not_called()
        mock_memory.add_message_pieces_to_memory_async.assert_not_called()
        mock_memory.update_attack_result_by_id_async.assert_not_called()

    async def test_rejects_source_conversation_from_different_attack(self, attack_service, mock_memory):
        """Should raise ValueError when source_conversation_id doesn't belong to the attack."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]

        request = CreateConversationRequest(source_conversation_id="unrelated-conv", cutoff_index=0)

        with pytest.raises(ValueError, match="not part of attack"):
            await attack_service.create_related_conversation_async(
                attack_result_id="ar-attack-1",
                request=request,
            )

        mock_memory.add_conversation_branches_to_attack_async.assert_not_called()

    @pytest.mark.parametrize("conversation_type", [ConversationType.ADVERSARIAL, ConversationType.PREPARATION])
    async def test_rejects_diagnostic_sources_async(
        self, *, attack_service: AttackService, mock_memory: MagicMock, conversation_type: ConversationType
    ) -> None:
        ar = make_attack_result()
        ar.related_conversations = {
            ConversationReference(conversation_id="diagnostic", conversation_type=conversation_type),
        }
        mock_memory.get_attack_results_async.return_value = [ar]

        with pytest.raises(ValueError, match="not part of attack"):
            await attack_service.create_related_conversation_async(
                attack_result_id="ar-attack-1",
                request=CreateConversationRequest(source_conversation_id="diagnostic", cutoff_index=0),
            )

        mock_memory.get_conversation_messages_async.assert_not_called()
        mock_memory.add_conversation_branches_to_attack_async.assert_not_called()

    async def test_branch_persistence_failure_does_not_commit_copies_async(
        self, *, attack_service: AttackService, mock_memory: MagicMock
    ) -> None:
        mock_memory.get_attack_results_async.return_value = [make_attack_result()]
        mock_memory.get_conversation_messages_async.return_value = [_make_message(role="user", sequence=0)]
        copied = MessagePiece(conversation_id="branch", role="user", original_value="copy")
        mock_memory.duplicate_messages_async.return_value = ("branch", [copied])
        mock_memory.add_conversation_branches_to_attack_async.side_effect = RuntimeError("branch insertion failed")

        with pytest.raises(RuntimeError, match="branch insertion failed"):
            await attack_service.create_related_conversation_async(
                attack_result_id="ar-attack-1",
                request=CreateConversationRequest(source_conversation_id="attack-1", cutoff_index=0),
            )

        assert mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs["message_pieces"] == [copied]
        mock_memory.add_conversation_to_memory_async.assert_not_called()
        mock_memory.add_message_pieces_to_memory_async.assert_not_called()
        mock_memory.update_attack_result_by_id_async.assert_not_called()


# ============================================================================
# Change Main Conversation Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestUpdateMainConversation:
    """Tests for update_main_conversation_async (promote related conversation to main)."""

    async def test_returns_none_when_attack_not_found(self, attack_service, mock_memory):
        """Should return None when the attack doesn't exist."""
        mock_memory.get_attack_results_async.return_value = []

        result = await attack_service.update_main_conversation_async(
            attack_result_id="missing",
            request=UpdateMainConversationRequest(conversation_id="conv-1"),
        )

        assert result is None

    async def test_noop_when_target_is_already_main(self, attack_service, mock_memory):
        """Memory must check the current main while holding the attack's write lock."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]

        result = await attack_service.update_main_conversation_async(
            attack_result_id="ar-attack-1",
            request=UpdateMainConversationRequest(conversation_id="attack-1"),
        )

        assert result is not None
        assert result.conversation_id == "attack-1"
        mock_memory.promote_attack_conversation_async.assert_called_once_with(
            attack_result_id="ar-attack-1", conversation_id="attack-1"
        )
        mock_memory.update_attack_result_by_id_async.assert_not_called()

    async def test_raises_when_conversation_not_part_of_attack(self, attack_service, mock_memory):
        """Should raise ValueError when conversation is not in the attack."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]

        with pytest.raises(ValueError, match="not part of this attack"):
            await attack_service.update_main_conversation_async(
                attack_result_id="ar-attack-1",
                request=UpdateMainConversationRequest(conversation_id="not-related"),
            )

        mock_memory.promote_attack_conversation_async.assert_not_called()

    async def test_swaps_main_conversation(self, attack_service, mock_memory):
        """Changing the main to a related conversation should swap it with the main."""
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(
                conversation_id="branch-1",
                conversation_type=ConversationType.PRUNED,
                description="Branch 1",
            ),
        }
        mock_memory.get_attack_results_async.return_value = [ar]

        result = await attack_service.update_main_conversation_async(
            attack_result_id="ar-attack-1",
            request=UpdateMainConversationRequest(conversation_id="branch-1"),
        )

        assert result is not None
        assert result.attack_result_id == "ar-attack-1"
        assert result.conversation_id == "branch-1"

        mock_memory.promote_attack_conversation_async.assert_called_once()
        call_kwargs = mock_memory.promote_attack_conversation_async.call_args.kwargs
        assert call_kwargs["attack_result_id"] == "ar-attack-1"
        assert call_kwargs["conversation_id"] == "branch-1"
        mock_memory.update_attack_result_by_id_async.assert_not_called()

    async def test_returns_none_when_attack_disappears_during_promotion_async(
        self, *, attack_service: AttackService, mock_memory: MagicMock
    ) -> None:
        mock_memory.get_attack_results_async.return_value = [make_attack_result()]
        mock_memory.promote_attack_conversation_async.return_value = False

        result = await attack_service.update_main_conversation_async(
            attack_result_id="ar-attack-1",
            request=UpdateMainConversationRequest(conversation_id="attack-1"),
        )

        assert result is None
        mock_memory.update_attack_result_by_id_async.assert_not_called()

    @pytest.mark.parametrize("conversation_type", ["preparation", "adversarial"])
    async def test_rejects_promoting_diagnostic_conversation(self, attack_service, mock_memory, conversation_type):
        """Diagnostic conversations cannot replace the evaluated main conversation."""
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(
                conversation_id="diagnostic-1",
                conversation_type=ConversationType(conversation_type),
            ),
        }
        mock_memory.get_attack_results_async.return_value = [ar]

        with pytest.raises(ValueError, match="not part of this attack"):
            await attack_service.update_main_conversation_async(
                attack_result_id="ar-attack-1",
                request=UpdateMainConversationRequest(conversation_id="diagnostic-1"),
            )

        mock_memory.promote_attack_conversation_async.assert_not_called()
        mock_memory.update_attack_result_by_id_async.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
class TestAddMessageTargetConversation:
    """Tests for add_message_async with target_conversation_id."""

    async def test_stores_message_in_target_conversation(self, attack_service, mock_memory):
        """When target_conversation_id is set, messages should go to that conversation."""
        from pyrit.backend.models.attacks import AttackSummary, ConversationMessagesResponse
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(conversation_id="branch-1", conversation_type=ConversationType.PRUNED),
        }
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []
        mock_memory.get_conversation_messages_async.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="text", original_value="Hello")],
            send=False,
            target_conversation_id="branch-1",
        )

        now = datetime.now(UTC)
        mock_summary = AttackSummary(
            attack_result_id="ar-attack-1",
            conversation_id="attack-1",
            objective="test objective",
            message_count=1,
            labels={},
            created_at=now,
            updated_at=now,
        )
        mock_messages = ConversationMessagesResponse(
            conversation_id="branch-1",
            messages=[],
        )

        with (
            patch.object(attack_service, "get_attack_async", return_value=mock_summary),
            patch.object(attack_service, "get_conversation_messages_async", return_value=mock_messages) as mock_msgs,
        ):
            await attack_service.add_message_async(attack_result_id="attack-1", request=request)

        # get_conversation_messages_async should be called with conversation_id=branch-1
        mock_msgs.assert_called_once_with(
            attack_result_id="attack-1",
            conversation_id="branch-1",
        )


@pytest.mark.usefixtures("patch_central_database")
class TestConversationCount:
    """Tests verifying conversation count is accurate in attack list."""

    async def test_list_attacks_includes_related_conversation_ids(self, attack_service, mock_memory):
        """Attacks with related conversations should expose them in the summary."""
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(
                conversation_id="branch-1",
                conversation_type=ConversationType.ADVERSARIAL,
            ),
            ConversationReference(
                conversation_id="branch-2",
                conversation_type=ConversationType.ADVERSARIAL,
            ),
        }
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []

        result = await attack_service.list_attacks_async()

        assert len(result.items) == 1
        assert sorted(result.items[0].related_conversation_ids) == ["branch-1", "branch-2"]

    async def test_list_attacks_no_related_returns_empty_list(self, attack_service, mock_memory):
        """An attack with no related conversations should return empty list."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []

        result = await attack_service.list_attacks_async()

        assert result.items[0].related_conversation_ids == []

    async def test_create_conversation_increments_count(self, attack_service, mock_memory):
        """Creating a related conversation should add to pruned_conversation_ids."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []

        result = await attack_service.create_related_conversation_async(
            attack_result_id="attack-1",
            request=CreateConversationRequest(),
        )

        call_kwargs = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs
        ids = [conversation.conversation_id for conversation in call_kwargs["conversations"]]
        assert result.conversation_id in ids
        assert len(ids) == 1

    async def test_create_second_conversation_preserves_first(self, attack_service, mock_memory):
        """Creating a second related conversation should keep the first one."""
        from pyrit.backend.models.attacks import CreateConversationRequest
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(
                conversation_id="conv-existing",
                conversation_type=ConversationType.PRUNED,
            ),
        }
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_message_pieces_async.return_value = []

        result = await attack_service.create_related_conversation_async(
            attack_result_id="attack-1",
            request=CreateConversationRequest(),
        )

        call_kwargs = mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs
        ids = [conversation.conversation_id for conversation in call_kwargs["conversations"]]
        assert result.conversation_id in ids
        assert len(ids) == 1
        mock_memory.update_attack_result_by_id_async.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
class TestConversationSorting:
    """Tests verifying conversations are sorted correctly."""

    async def test_conversations_sorted_by_created_at_earliest_first(self, attack_service, mock_memory):
        """Conversations should be sorted by created_at with earliest first."""
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(
                conversation_id="branch-1",
                conversation_type=ConversationType.PRUNED,
            ),
        }
        mock_memory.get_attack_results_async.return_value = [ar]

        t_early = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
        t_late = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)

        mock_memory.get_conversation_stats_async.return_value = {
            "attack-1": ConversationStats(message_count=1, last_message_preview="test", created_at=t_late),
            "branch-1": ConversationStats(message_count=1, last_message_preview="test", created_at=t_early),
        }

        result = await attack_service.get_conversations_async(attack_result_id="attack-1")

        assert result is not None
        # branch-1 (earlier) should come before attack-1 (later)
        assert result.conversations[0].conversation_id == "branch-1"
        assert result.conversations[1].conversation_id == "attack-1"

    async def test_empty_conversations_sorted_last(self, attack_service, mock_memory):
        """Conversations with no timestamp should appear at the bottom."""
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(
                conversation_id="empty-conv",
                conversation_type=ConversationType.PRUNED,
            ),
        }
        mock_memory.get_attack_results_async.return_value = [ar]

        t = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)

        mock_memory.get_conversation_stats_async.return_value = {
            "attack-1": ConversationStats(message_count=1, last_message_preview="test", created_at=t),
        }

        result = await attack_service.get_conversations_async(attack_result_id="attack-1")

        assert result is not None
        # attack-1 (has timestamp) should come before empty-conv (no timestamp)
        assert result.conversations[0].conversation_id == "attack-1"
        assert result.conversations[1].conversation_id == "empty-conv"

    async def test_empty_conversations_all_sort_last(self, attack_service, mock_memory):
        """Multiple empty conversations should all have created_at=None."""
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(
                conversation_id="new-conv",
                conversation_type=ConversationType.PRUNED,
            ),
        }
        mock_memory.get_attack_results_async.return_value = [ar]
        mock_memory.get_conversation_stats_async.return_value = {}  # Both have no stats

        result = await attack_service.get_conversations_async(attack_result_id="attack-1")

        assert result is not None
        # Both empty conversations should have created_at=None
        for conv in result.conversations:
            assert conv.created_at is None


@pytest.mark.usefixtures("patch_central_database")
class TestAttackServiceAdditionalCoverage:
    """Targeted branch coverage tests for attack service helpers and converter merge logic."""

    @pytest.mark.parametrize("has_metadata", [False, True])
    @pytest.mark.parametrize("has_pieces", [False, True])
    @pytest.mark.parametrize("has_target", [False, True])
    async def test_create_related_conversation_uses_duplicate_branch(
        self,
        *,
        attack_service: AttackService,
        mock_memory: MagicMock,
        has_metadata: bool,
        has_pieces: bool,
        has_target: bool,
    ) -> None:
        """Prepare the history without committing it before the atomic branch insertion."""
        from pyrit.backend.models.attacks import CreateConversationRequest
        from pyrit.models import Conversation

        ar = make_attack_result(conversation_id="attack-1", has_target=has_target)
        mock_memory.get_attack_results_async.return_value = [ar]
        expected_target = (
            ComponentIdentifier(
                class_name="SourceTarget" if has_metadata else "TextTarget", class_module="pyrit.prompt_target"
            )
            if has_metadata or has_target
            else None
        )
        source = Conversation(conversation_id="attack-1", target_identifier=expected_target)
        mock_memory.get_conversation_metadata_async.return_value = source if has_metadata else None
        prepared = Conversation(conversation_id="branch-dup", target_identifier=expected_target)
        pieces = [MessagePiece(conversation_id="branch-dup", role="user", original_value="copy")] if has_pieces else []

        with patch.object(
            attack_service, "_prepare_conversation_up_to_async", return_value=(prepared, pieces)
        ) as mock_dup:
            result = await attack_service.create_related_conversation_async(
                attack_result_id="attack-1",
                request=CreateConversationRequest(source_conversation_id="attack-1", cutoff_index=2),
            )

        assert result is not None
        assert result.conversation_id == "branch-dup"
        mock_dup.assert_called_once()
        assert mock_dup.call_args.kwargs == {
            "source_conversation_id": "attack-1",
            "cutoff_index": 2,
            "target_identifier": expected_target,
        }
        mock_memory.add_conversation_branches_to_attack_async.assert_called_once()
        assert mock_memory.add_conversation_branches_to_attack_async.call_args.kwargs == {
            "attack_result_id": "attack-1",
            "conversations": [prepared],
            "message_pieces": pieces,
            "source_conversation": source,
        }
        mock_memory.add_conversation_to_memory_async.assert_not_called()
        mock_memory.add_message_pieces_to_memory_async.assert_not_called()

    @pytest.mark.parametrize("cutoff_index,expected_sequences", [(-1, []), (0, [0]), (2, [0, 2])])
    async def test_prepare_conversation_up_to_does_not_persist(
        self,
        *,
        attack_service: AttackService,
        mock_memory: MagicMock,
        cutoff_index: int,
        expected_sequences: list[int],
    ) -> None:
        mock_memory.get_conversation_messages_async.return_value = [
            _make_message(role="user", sequence=sequence) for sequence in [0, 2, 4]
        ]
        pieces = [
            MessagePiece(conversation_id="prepared", role="user", original_value="copy", sequence=sequence)
            for sequence in expected_sequences
        ]
        mock_memory.duplicate_messages_async.return_value = ("prepared", pieces)
        target = ComponentIdentifier(class_name="TextTarget", class_module="pyrit.prompt_target")

        conversation, copies = await attack_service._prepare_conversation_up_to_async(
            source_conversation_id="source", cutoff_index=cutoff_index, target_identifier=target
        )

        assert conversation == Conversation(conversation_id="prepared", target_identifier=target)
        assert copies == pieces
        mock_memory.get_conversation_messages_async.assert_called_once_with(conversation_id="source")
        assert [
            message.sequence for message in mock_memory.duplicate_messages_async.call_args.kwargs["messages"]
        ] == expected_sequences
        mock_memory.add_conversation_to_memory_async.assert_not_called()
        mock_memory.add_message_pieces_to_memory_async.assert_not_called()

    async def test_prepare_empty_messages_does_not_write_async(
        self, *, attack_service: AttackService, mock_memory: MagicMock
    ) -> None:
        pieces = await attack_service._prepare_message_pieces_async(
            conversation_id="conv-1", messages=[], persisted_paths=[]
        )
        assert pieces == []
        mock_memory.add_conversation_to_memory_async.assert_not_called()
        mock_memory.add_message_pieces_to_memory_async.assert_not_called()

    @pytest.mark.parametrize(("role", "expected"), [("assistant", "simulated_assistant"), ("tool", "simulated_tool")])
    async def test_prepare_conversation_remaps_response_roles_async(
        self, *, attack_service: AttackService, mock_memory: MagicMock, role: ChatMessageRole, expected: ChatMessageRole
    ) -> None:
        piece = MessagePiece(conversation_id="branch-1", role=role, sequence=0, original_value="copied")
        mock_memory.duplicate_messages_async.return_value = ("branch-1", [piece])

        _, copies = await attack_service._prepare_conversation_up_to_async(
            source_conversation_id="attack-1", cutoff_index=0
        )

        assert copies[0].role == expected
        assert copies[0].prompt_metadata[MessagePiece.PREPENDED_HISTORY_METADATA_KEY]
        mock_memory.add_conversation_to_memory_async.assert_not_called()
        mock_memory.add_message_pieces_to_memory_async.assert_not_called()


def test_create_attack_request_normalizes_legacy_attribution_labels() -> None:
    labels = {"operator": "alice", "operation": "nightly", "team": "red"}

    with pytest.warns(DeprecationWarning, match="removed in 1.4.0"):
        request = CreateAttackRequest(target_registry_name="target", labels=labels)

    assert request.operator == "alice"
    assert request.operation == "nightly"
    assert request.labels == {"team": "red"}
    assert labels == {"operator": "alice", "operation": "nightly", "team": "red"}


def test_create_attack_request_rejects_overlength_values() -> None:
    with pytest.raises(ValueError, match="at most 128"):
        CreateAttackRequest(target_registry_name="target", operator="x" * 129)
