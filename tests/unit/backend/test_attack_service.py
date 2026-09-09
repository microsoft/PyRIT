# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for attack service.

The attack service uses PyRIT memory with AttackResult as the source of truth.
"""

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.models.attacks import (
    AddMessageRequest,
    AttackSummary,
    ConversationMessagesResponse,
    ConverterConfigurationRequest,
    CreateAttackRequest,
    MessagePieceRequest,
    PrependedMessageRequest,
    UpdateAttackRequest,
    UpdateMainConversationRequest,
)
from pyrit.backend.services.attack_service import (
    AttackService,
    get_attack_service,
)
from pyrit.backend.services.pagination import (
    decode_keyset_cursor,
    encode_keyset_cursor,
    fingerprint_filters,
    normalize_label_filters,
)
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Message,
    MessagePiece,
)
from pyrit.models.conversation_stats import ConversationStats
from pyrit.prompt_normalizer import ConverterConfiguration


@pytest.fixture
def mock_memory():
    """Create a mock memory instance."""
    memory = MagicMock()
    memory.get_attack_results.return_value = []
    memory.get_conversation_messages.return_value = []
    memory.get_message_pieces.return_value = []
    memory.get_conversation_stats.return_value = {}
    memory._get_conversation.return_value = None
    memory.get_prompt_scores.return_value = []

    return memory


@pytest.fixture
def attack_service(mock_memory):
    """Create an attack service with mocked memory."""
    with patch("pyrit.backend.services.attack_service.CentralMemory") as mock_central:
        mock_central.get_memory_instance.return_value = mock_memory
        service = AttackService()
        yield service


def make_attack_result(
    *,
    conversation_id: str = "attack-1",
    attack_result_id: str = "",
    objective: str = "Test objective",
    has_target: bool = True,
    name: str = "Test Attack",
    outcome: AttackOutcome = AttackOutcome.UNDETERMINED,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> AttackResult:
    """Create a mock AttackResult for testing."""
    now = datetime.now(timezone.utc)
    created = created_at or now
    updated = updated_at or now

    # Default attack_result_id to "ar-<conversation_id>" when not explicit.
    effective_ar_id = attack_result_id or f"ar-{conversation_id}"

    target_identifier = (
        ComponentIdentifier(
            class_name="TextTarget",
            class_module="pyrit.prompt_target",
        )
        if has_target
        else None
    )

    return AttackResult(
        conversation_id=conversation_id,
        objective=objective,
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name=name,
                class_module="pyrit.backend",
                children={"objective_target": target_identifier} if target_identifier else {},
            ),
        ),
        outcome=outcome,
        attack_result_id=effective_ar_id,
        timestamp=updated,
        metadata={
            "created_at": created.isoformat(),
            "updated_at": updated.isoformat(),
        },
        labels={"test_ar_label": "test_ar_value"},
    )


def _make_matching_target_mock() -> MagicMock:
    """Create a mock target object whose get_identifier() matches make_attack_result's default target."""
    mock_target = MagicMock()
    mock_target.get_identifier.return_value = ComponentIdentifier(
        class_name="TextTarget",
        class_module="pyrit.prompt_target",
    )
    return mock_target


async def _send_message_and_get_update_fields(
    *,
    attack_service: AttackService,
    mock_memory: MagicMock,
    attack_result_id: str,
    request: AddMessageRequest,
    attack_result: AttackResult,
    converter_identifiers: list[ComponentIdentifier],
) -> dict[str, Any]:
    """
    Send a message with common mocks and return the attack result update fields.

    Args:
        attack_service: The service under test.
        mock_memory: The mocked memory instance used by the service.
        attack_result_id: The attack result identifier passed to the service.
        request: The message request to send.
        attack_result: The attack result returned by memory.
        converter_identifiers: The identifiers returned by resolved converters.

    Returns:
        dict[str, Any]: The fields used to update the attack result.
    """
    mock_memory.get_attack_results.return_value = [attack_result]
    mock_memory.get_message_pieces.return_value = []

    converter_objects: list[MagicMock] = []
    for identifier in converter_identifiers:
        converter = MagicMock()
        converter.get_identifier.return_value = identifier
        converter_objects.append(converter)

    now = datetime.now(timezone.utc)
    with (
        patch("pyrit.backend.services.attack_service.get_converter_service") as mock_get_converter_service,
        patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_service,
        patch("pyrit.backend.services.attack_service.PromptNormalizer") as mock_normalizer_class,
        patch.object(
            attack_service,
            "get_attack_async",
            new=AsyncMock(
                return_value=AttackSummary(
                    attack_result_id=attack_result_id,
                    conversation_id=request.target_conversation_id,
                    objective=attack_result.objective,
                    message_count=0,
                    labels={},
                    created_at=now,
                    updated_at=now,
                )
            ),
        ),
        patch.object(
            attack_service,
            "get_conversation_messages_async",
            new=AsyncMock(
                return_value=ConversationMessagesResponse(
                    conversation_id=request.target_conversation_id,
                    messages=[],
                )
            ),
        ),
    ):
        mock_converter_service = MagicMock()
        mock_converter_service.get_converter_objects_for_ids.return_value = converter_objects
        mock_get_converter_service.return_value = mock_converter_service
        mock_get_target_service.return_value.get_target_object.return_value = _make_matching_target_mock()
        mock_normalizer_class.return_value.send_prompt_async = AsyncMock()

        await attack_service.add_message_async(attack_result_id=attack_result_id, request=request)

    return mock_memory.update_attack_result_by_id.call_args.kwargs["update_fields"]


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


def _make_round_robin_identifier(
    *,
    second_model_name: str = "e2e-dummy-model",
    weights: tuple[int, int] = (1, 1),
) -> ComponentIdentifier:
    """Create a composite target identifier with no root endpoint or model."""
    return ComponentIdentifier(
        class_name="RoundRobinTarget",
        class_module="pyrit.prompt_target.round_robin_target",
        params={"weights": list(weights)},
        children={
            "targets": [
                ComponentIdentifier(
                    class_name="TextTarget",
                    class_module="pyrit.prompt_target",
                    params={"model_name": "e2e-dummy-model"},
                ),
                ComponentIdentifier(
                    class_name="TextTarget",
                    class_module="pyrit.prompt_target",
                    params={"model_name": second_model_name},
                ),
            ]
        },
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
    piece.timestamp = timestamp or datetime.now(timezone.utc)
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
        with patch("pyrit.backend.services.attack_service.CentralMemory") as mock_central:
            mock_memory = MagicMock()
            mock_central.get_memory_instance.return_value = mock_memory

            service = AttackService()

            mock_central.get_memory_instance.assert_called_once()
            assert service._memory == mock_memory


# ============================================================================
# List Attacks Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestListAttacks:
    """Tests for list_attacks method."""

    async def test_list_attacks_returns_empty_when_no_attacks(self, attack_service, mock_memory) -> None:
        """Test that list_attacks returns empty list when no AttackResults exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.list_attacks_async()

        assert result.items == []
        assert result.pagination.has_more is False

    async def test_list_attacks_returns_attacks(self, attack_service, mock_memory) -> None:
        """Test that list_attacks returns attacks from AttackResult records."""
        ar = make_attack_result()
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async()

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-1"
        assert result.items[0].attack_type == "Test Attack"

    async def test_list_attacks_filters_by_attack_types_exact(self, attack_service, mock_memory) -> None:
        """Test that list_attacks passes attack_types to memory layer."""
        ar1 = make_attack_result(conversation_id="attack-1", name="CrescendoAttack")
        mock_memory.get_attack_results.return_value = [ar1]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async(attack_types=["CrescendoAttack"])

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-1"
        # Verify attack_types was forwarded to the memory layer as attack_classes
        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["attack_classes"] == ["CrescendoAttack"]

    async def test_list_attacks_attack_types_passed_to_memory(self, attack_service, mock_memory) -> None:
        """Test that attack_types is forwarded to memory as attack_classes for DB-level filtering."""
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(attack_types=["Crescendo"])

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["attack_classes"] == ["Crescendo"]

    async def test_list_attacks_filters_by_attack_types_multi(self, attack_service, mock_memory) -> None:
        """Test that multiple attack_types are forwarded as a list to memory for OR-matching."""
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(attack_types=["CrescendoAttack", "ManualAttack"])

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["attack_classes"] == ["CrescendoAttack", "ManualAttack"]

    async def test_list_attacks_attack_types_empty_list_coerced_to_none(self, attack_service, mock_memory) -> None:
        """Test that attack_types=[] is coerced to None before reaching memory (no filter)."""
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(attack_types=[])

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["attack_classes"] is None

    async def test_list_attacks_coerces_empty_converter_types_to_no_filter(self, attack_service, mock_memory) -> None:
        """converter_types=[] at the service boundary means 'no converter filter'.

        The 'attacks with no converters' intent is expressed via has_converters=False;
        an empty list is coerced to None so route/service/memory stay consistent.
        """
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(converter_types=[])

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["converter_classes"] is None
        assert call_kwargs["has_converters"] is None

    async def test_list_attacks_forwards_has_converters_true(self, attack_service, mock_memory) -> None:
        """has_converters=True is forwarded to memory."""
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(has_converters=True)

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["has_converters"] is True

    async def test_list_attacks_forwards_has_converters_false(self, attack_service, mock_memory) -> None:
        """has_converters=False is forwarded to memory."""
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(has_converters=False)

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["has_converters"] is False

    async def test_list_attacks_forwards_scenario_attack_filter(self, attack_service, mock_memory) -> None:
        """The scenario-attack inclusion flag is forwarded to memory."""
        mock_memory.get_attack_results.return_value = []

        await attack_service.list_attacks_async(include_scenario_attacks=False)

        assert mock_memory.get_attack_results.call_args.kwargs["include_scenario_attacks"] is False

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
        mock_memory.get_attack_results.return_value = [ar1]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async(converter_types=["Base64Converter", "ROT13Converter"])

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-1"
        # Verify converter_types was forwarded to the memory layer with default "all" mode
        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["converter_classes"] == ["Base64Converter", "ROT13Converter"]
        assert call_kwargs["converter_classes_match"] == "all"

    async def test_list_attacks_converter_match_all_explicit_pushes_to_memory(
        self, attack_service, mock_memory
    ) -> None:
        """Explicit converter_types_match='all' still pushes converter filter to memory."""
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(
            converter_types=["Base64Converter", "ROT13Converter"],
            converter_types_match="all",
        )

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["converter_classes"] == ["Base64Converter", "ROT13Converter"]
        assert call_kwargs["converter_classes_match"] == "all"

    async def test_list_attacks_converter_match_any_single_converter_pushes_to_memory(
        self, attack_service, mock_memory
    ) -> None:
        """Degenerate case: converter_types_match='any' with one converter still pushes to memory.

        The memory layer ignores the match mode when the list has fewer than 2 entries, but the
        service still forwards the mode verbatim (memory is authoritative for that optimization).
        """
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(
            converter_types=["Base64Converter"],
            converter_types_match="any",
        )

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["converter_classes"] == ["Base64Converter"]
        assert call_kwargs["converter_classes_match"] == "any"

    async def test_list_attacks_converter_match_any_pushes_to_memory(self, attack_service, mock_memory) -> None:
        """converter_types_match='any' with 2+ converters pushes down to the DB via memory.

        Previously this branch loaded every row matching other filters into Python and filtered
        with a set intersection, which was O(total rows) per query. The OR-matching is now
        expressed as a DB predicate so only matching rows are returned and pagination is honored.
        """
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(
            converter_types=["Base64Converter", "ROT13Converter"],
            converter_types_match="any",
        )

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["converter_classes"] == ["Base64Converter", "ROT13Converter"]
        assert call_kwargs["converter_classes_match"] == "any"

    async def test_list_attacks_forwards_min_turns(self, attack_service, mock_memory) -> None:
        """min_turns is forwarded to the memory query (filtering now happens in SQL)."""
        mock_memory.get_attack_results.return_value = []

        await attack_service.list_attacks_async(min_turns=3)

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["min_turns"] == 3

    async def test_list_attacks_forwards_max_turns(self, attack_service, mock_memory) -> None:
        """max_turns is forwarded to the memory query (filtering now happens in SQL)."""
        mock_memory.get_attack_results.return_value = []

        await attack_service.list_attacks_async(max_turns=3)

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["max_turns"] == 3

    async def test_list_attacks_includes_labels_in_summary(self, attack_service, mock_memory) -> None:
        """Test that list_attacks includes labels from conversation stats in summaries."""
        ar = make_attack_result(
            conversation_id="attack-1",
        )
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_stats.return_value = {
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
        mock_memory.get_attack_results.return_value = [ar]
        path = r"C:\Users\someone\PyRIT\dbdata\prompt-memory-entries\images\1780010098266691.png"
        mock_memory.get_conversation_stats.return_value = {
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

    async def test_list_attacks_filters_by_labels_directly(self, attack_service, mock_memory) -> None:
        """Test that label filters are passed directly to the DB query (no legacy expansion)."""
        ar = make_attack_result(conversation_id="attack-canonical")

        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_stats.side_effect = lambda conversation_ids: {
            cid: ConversationStats(message_count=1, labels={"operator": "alice", "operation": "red"})
            for cid in conversation_ids
        }

        result = await attack_service.list_attacks_async(labels={"operator": "alice", "operation": "red"})

        assert len(result.items) == 1
        mock_memory.get_attack_results.assert_called_once()
        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["labels"] == {"operator": "alice", "operation": "red"}

    async def test_list_attacks_forwards_min_and_max_turns(self, attack_service, mock_memory) -> None:
        """Both min_turns and max_turns are forwarded to the memory query."""
        mock_memory.get_attack_results.return_value = []

        await attack_service.list_attacks_async(min_turns=2, max_turns=5)

        call_kwargs = mock_memory.get_attack_results.call_args[1]
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
        mock_memory.get_unique_attack_class_names.return_value = []

        result = await attack_service.get_attack_options_async()

        assert result == []
        mock_memory.get_unique_attack_class_names.assert_called_once()

    async def test_returns_result_from_memory(self, attack_service, mock_memory) -> None:
        """Test that attack options delegates to memory layer."""
        mock_memory.get_unique_attack_class_names.return_value = ["CrescendoAttack", "ManualAttack"]

        result = await attack_service.get_attack_options_async()

        assert result == ["CrescendoAttack", "ManualAttack"]
        mock_memory.get_unique_attack_class_names.assert_called_once()


# ============================================================================
# Converter Options Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestConverterOptions:
    """Tests for get_converter_options_async method."""

    async def test_returns_empty_when_no_attacks(self, attack_service, mock_memory) -> None:
        """Test that converter options returns empty list when no attacks exist."""
        mock_memory.get_unique_converter_class_names.return_value = []

        result = await attack_service.get_converter_options_async()

        assert result == []
        mock_memory.get_unique_converter_class_names.assert_called_once()

    async def test_returns_result_from_memory(self, attack_service, mock_memory) -> None:
        """Test that converter options delegates to memory layer."""
        mock_memory.get_unique_converter_class_names.return_value = ["Base64Converter", "ROT13Converter"]

        result = await attack_service.get_converter_options_async()

        assert result == ["Base64Converter", "ROT13Converter"]
        mock_memory.get_unique_converter_class_names.assert_called_once()


# ============================================================================
# Get Attack Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestGetAttack:
    """Tests for get_attack method."""

    async def test_get_attack_returns_none_for_nonexistent(self, attack_service, mock_memory) -> None:
        """Test that get_attack returns None when AttackResult doesn't exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.get_attack_async(attack_result_id="nonexistent")

        assert result is None

    async def test_get_attack_returns_attack_details(self, attack_service, mock_memory) -> None:
        """Test that get_attack returns attack details from AttackResult."""
        ar = make_attack_result(
            conversation_id="test-id",
            name="My Attack",
        )
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

        result = await attack_service.get_attack_async(attack_result_id="test-id")

        assert result is not None
        assert result.conversation_id == "test-id"
        assert result.attack_type == "My Attack"


# ============================================================================
# Get Conversation Messages Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestGetConversationMessages:
    """Tests for get_conversation_messages method."""

    async def test_get_conversation_messages_returns_none_for_nonexistent(self, attack_service, mock_memory) -> None:
        """Test that get_conversation_messages returns None when attack doesn't exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="nonexistent", conversation_id="any-id"
        )

        assert result is None

    async def test_get_conversation_messages_returns_messages(self, attack_service, mock_memory) -> None:
        """Test that get_conversation_messages returns messages for existing attack."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="test-id", conversation_id="test-id"
        )

        assert result is not None
        assert result.conversation_id == "test-id"
        assert result.messages == []

    async def test_get_conversation_messages_marks_attack_objective_score(self, attack_service, mock_memory) -> None:
        """The message mapper receives the attack's canonical objective score ID."""
        ar = make_attack_result(conversation_id="test-id")
        objective_score_id = uuid.uuid4()
        ar.last_score = MagicMock(id=objective_score_id)
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

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
        ar.last_score = MagicMock(id=objective_score_id)
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

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
        """Test that get_conversation_messages raises ValueError for a conversation not belonging to the attack."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

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
                request=CreateAttackRequest(target_registry_name="target-1", name="My Attack")
            )

            assert result.conversation_id is not None
            assert result.created_at is not None
            mock_memory.add_attack_results_to_memory.assert_called_once()
            stored_attack = mock_memory.add_attack_results_to_memory.call_args.kwargs["attack_results"][0]
            assert stored_attack.metadata["target_registry_name"] == "target-1"

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
            # Both attack result and prepended message pieces should be stored
            mock_memory.add_attack_results_to_memory.assert_called_once()
            mock_memory.add_message_pieces_to_memory.assert_called()

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

            calls = mock_memory.add_message_pieces_to_memory.call_args_list
            assert len(calls) == 1
            piece = calls[0][1]["message_pieces"][0]
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

            mock_memory.add_message_pieces_to_memory.assert_not_called()

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

            calls = mock_memory.add_message_pieces_to_memory.call_args_list
            assert len(calls) == 2
            roles = [call[1]["message_pieces"][0].api_role for call in calls]
            sequences = [call[1]["message_pieces"][0].sequence for call in calls]
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

            call_args = mock_memory.add_attack_results_to_memory.call_args
            stored_ar = call_args[1]["attack_results"][0]
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

            stored_ar = mock_memory.add_attack_results_to_memory.call_args[1]["attack_results"][0]
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

            # Each message stored separately with incrementing sequence
            calls = mock_memory.add_message_pieces_to_memory.call_args_list
            assert len(calls) == 3
            sequences = [call[1]["message_pieces"][0].sequence for call in calls]
            assert sequences == [0, 1, 2]

            roles = [call[1]["message_pieces"][0].api_role for call in calls]
            assert roles == ["system", "user", "assistant"]

            # original_prompt_id preserved for lineage tracking
            import uuid

            stored_pieces = [call[1]["message_pieces"][0] for call in calls]
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

            stored_ar = mock_memory.add_attack_results_to_memory.call_args[1]["attack_results"][0]
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

            call_args = mock_memory.add_attack_results_to_memory.call_args
            stored_ar = call_args[1]["attack_results"][0]
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

            stored_ar = mock_memory.add_attack_results_to_memory.call_args[1]["attack_results"][0]
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
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.update_attack_async(
            attack_result_id="nonexistent", request=UpdateAttackRequest(outcome="success")
        )

        assert result is None

    async def test_update_attack_updates_outcome_success(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'success' to AttackOutcome.SUCCESS."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="success")
        )

        mock_memory.update_attack_result_by_id.assert_called_once()
        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["attack_result_id"] == "test-id"
        assert call_kwargs["update_fields"]["outcome"] == "success"

    async def test_update_attack_updates_outcome_failure(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'failure' to AttackOutcome.FAILURE."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="failure")
        )

        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["update_fields"]["outcome"] == "failure"

    async def test_update_attack_updates_outcome_undetermined(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'undetermined' to AttackOutcome.UNDETERMINED."""
        ar = make_attack_result(conversation_id="test-id", outcome=AttackOutcome.SUCCESS)
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="undetermined")
        )

        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["update_fields"]["outcome"] == "undetermined"

    async def test_update_attack_updates_outcome_error(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'error' to AttackOutcome.ERROR."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="error")
        )

        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["update_fields"]["outcome"] == "error"

    async def test_update_attack_bumps_timestamp(self, attack_service, mock_memory) -> None:
        """Test that update_attack bumps the timestamp recency column and does not write metadata."""
        old_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ar = make_attack_result(conversation_id="test-id", updated_at=old_time)
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="success")
        )

        update_fields = mock_memory.update_attack_result_by_id.call_args[1]["update_fields"]
        assert isinstance(update_fields["timestamp"], datetime)
        assert update_fields["timestamp"] > old_time
        assert "attack_metadata" not in update_fields


# ============================================================================
# Add Message Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestAddMessage:
    """Tests for add_message method."""

    async def test_add_message_raises_for_nonexistent_attack(self, attack_service, mock_memory) -> None:
        """Test that add_message raises ValueError for nonexistent attack."""
        mock_memory.get_attack_results.return_value = []

        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
        )

        with pytest.raises(ValueError, match="not found"):
            await attack_service.add_message_async(attack_result_id="nonexistent", request=request)

    async def test_add_message_raises_when_send_without_registry_name(self, attack_service, mock_memory) -> None:
        """Test that add_message raises ValueError when send=True but target_registry_name missing."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=True,
        )

        with pytest.raises(ValueError, match="target_registry_name is required when send=True"):
            await attack_service.add_message_async(attack_result_id="test-id", request=request)

    async def test_add_message_send_false_without_registry_name_succeeds(self, attack_service, mock_memory) -> None:
        """Test that add_message with send=False does not require target_registry_name."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

        request = AddMessageRequest(
            role="system",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        result = await attack_service.add_message_async(attack_result_id="test-id", request=request)
        assert result.attack is not None

    async def test_add_message_with_send_sends_via_normalizer(self, attack_service, mock_memory) -> None:
        """Test that add_message with send=True sends message via normalizer."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.attack_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="test-target",
            )

            result = await attack_service.add_message_async(attack_result_id="test-id", request=request)

            mock_normalizer.send_prompt_async.assert_called_once()
            assert result.attack is not None

    async def test_add_message_with_send_raises_when_target_not_found(self, attack_service, mock_memory) -> None:
        """Test that add_message with send=True raises when target object not found."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc:
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = None
            mock_get_target_svc.return_value = mock_target_svc

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="test-target",
            )

            with pytest.raises(ValueError, match="Target object .* not found"):
                await attack_service.add_message_async(attack_result_id="test-id", request=request)

    async def test_add_message_surfaces_stored_error_piece_on_send_failure(self, attack_service, mock_memory) -> None:
        """When the normalizer stores an error piece then raises, the send returns that turn inline (no raise)."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

        # The PromptNormalizer persists a full error piece before re-raising; model
        # that by flipping to return the stored error piece only after send fails.
        traceback_text = "Connection error.\nAPIConnectionError('Connection error.')\nTraceback..."
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
        mock_memory.get_message_pieces.side_effect = lambda **_: [error_piece] if state["sent"] else []
        # The conversation-messages read (used to build the response DTO) must include
        # the stored error turn so we can assert it is surfaced to the caller.
        mock_memory.get_conversation_messages.side_effect = lambda **_: (
            [Message(message_pieces=[error_piece])] if state["sent"] else []
        )

        async def _raise_after_store(**_):
            state["sent"] = True
            raise Exception("Error sending prompt with conversation ID: test-id")

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.attack_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock(side_effect=_raise_after_store)
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

    async def test_add_message_reraises_when_send_fails_without_stored_error_piece(
        self, attack_service, mock_memory
    ) -> None:
        """If the send fails but no error piece was stored, the exception propagates (real error)."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []  # no error piece ever stored
        mock_memory.get_conversation_messages.return_value = []

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.attack_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock(side_effect=RuntimeError("boom"))
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="test-target",
            )

            with pytest.raises(RuntimeError, match="boom"):
                await attack_service.add_message_async(attack_result_id="test-id", request=request)

    async def test_add_message_with_legacy_converter_ids_warns_and_preserves_behavior(
        self, attack_service, mock_memory
    ) -> None:
        """Test that legacy converter IDs warn and remain an unrestricted pipeline."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.attack_service.get_converter_service") as mock_get_conv_svc,
            patch("pyrit.backend.services.attack_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            first_converter = MagicMock()
            first_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="FirstConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            second_converter = MagicMock()
            second_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="SecondConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            mock_conv_svc = MagicMock()
            mock_conv_svc.get_converter_objects_for_ids.return_value = [first_converter, second_converter]
            mock_get_conv_svc.return_value = mock_conv_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                converter_ids=["first", "second"],
                target_registry_name="test-target",
            )

            with pytest.warns(DeprecationWarning, match="AddMessageRequest.converter_ids is deprecated"):
                await attack_service.add_message_async(attack_result_id="test-id", request=request)

            configurations = mock_normalizer.send_prompt_async.call_args.kwargs["request_converter_configurations"]
            assert [configuration.converters for configuration in configurations] == [
                [first_converter],
                [second_converter],
            ]
            assert all(configuration.indexes_to_apply is None for configuration in configurations)
            mock_conv_svc.get_converter_objects_for_ids.assert_called_once_with(converter_ids=["first", "second"])

    def test_empty_legacy_converter_ids_allow_store_only_request(self) -> None:
        """Test that an empty legacy converter list remains a no-op when send is false."""
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
            converter_ids=[],
        )

        assert request.converter_ids == []

    def test_empty_legacy_converter_ids_warn_and_use_structured_configuration(self, attack_service) -> None:
        """Test that an empty legacy list does not override a structured configuration."""
        converter = MagicMock()
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            converter_ids=[],
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["structured"])],
        )

        with patch("pyrit.backend.services.attack_service.get_converter_service") as mock_get_converter_service:
            mock_get_converter_service.return_value.get_converter_objects_for_ids.return_value = [converter]

            with pytest.warns(DeprecationWarning, match="AddMessageRequest.converter_ids is deprecated"):
                configurations = attack_service._resolve_request_converter_configs(request=request)

        assert len(configurations) == 1
        assert configurations[0].converters == [converter]
        mock_get_converter_service.return_value.get_converter_objects_for_ids.assert_called_once_with(
            converter_ids=["structured"]
        )

    async def test_add_message_preserves_converter_configuration_targeting(self, attack_service, mock_memory) -> None:
        """Test that request and response converter targeting reaches the normalizer."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.attack_service.get_converter_service") as mock_get_conv_svc,
            patch("pyrit.backend.services.attack_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_conv_svc = MagicMock()
            first_request_converter = MagicMock()
            first_request_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="FirstRequestConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            second_request_converter = MagicMock()
            second_request_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="SecondRequestConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            third_request_converter = MagicMock()
            third_request_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="ThirdRequestConverter",
                class_module="test_module",
                params={"supported_input_types": ("image_path",), "supported_output_types": ("image_path",)},
            )
            response_converter = MagicMock()
            response_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="ResponseConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            converters_by_ids = {
                ("request-1", "request-2"): [first_request_converter, second_request_converter],
                ("request-3",): [third_request_converter],
                ("response-1",): [response_converter],
            }
            mock_conv_svc.get_converter_objects_for_ids.side_effect = lambda *, converter_ids: converters_by_ids[
                tuple(converter_ids)
            ]
            mock_get_conv_svc.return_value = mock_conv_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[
                    MessagePieceRequest(original_value="Hello"),
                    MessagePieceRequest(
                        data_type="image_path",
                        original_value="https://example.com/image.png",
                    ),
                ],
                target_conversation_id="test-id",
                send=True,
                request_converter_configurations=[
                    ConverterConfigurationRequest(
                        converter_ids=["request-1", "request-2"],
                        indexes_to_apply=[0],
                        prompt_data_types_to_apply=["text"],
                    ),
                    ConverterConfigurationRequest(
                        converter_ids=["request-3"],
                        indexes_to_apply=[1],
                        prompt_data_types_to_apply=["image_path"],
                    ),
                ],
                response_converter_configurations=[
                    ConverterConfigurationRequest(
                        converter_ids=["response-1"],
                        indexes_to_apply=[1],
                        prompt_data_types_to_apply=["text"],
                    )
                ],
                target_registry_name="test-target",
            )

            await attack_service.add_message_async(attack_result_id="test-id", request=request)

            call_kwargs = mock_normalizer.send_prompt_async.call_args.kwargs
            request_configs = call_kwargs["request_converter_configurations"]
            assert request_configs[0].converters == [first_request_converter, second_request_converter]
            assert request_configs[0].indexes_to_apply == [0]
            assert request_configs[0].prompt_data_types_to_apply == ["text"]
            assert request_configs[1].converters == [third_request_converter]
            assert request_configs[1].indexes_to_apply == [1]
            assert request_configs[1].prompt_data_types_to_apply == ["image_path"]
            response_config = call_kwargs["response_converter_configurations"][0]
            assert response_config.converters == [response_converter]
            assert response_config.indexes_to_apply == [1]
            assert response_config.prompt_data_types_to_apply == ["text"]

            update_fields = mock_memory.update_attack_result_by_id.call_args.kwargs["update_fields"]
            updated_atomic = AtomicAttackIdentifier.model_validate(update_fields["atomic_attack_identifier"])
            updated_attack = updated_atomic.attack_technique.attack
            assert [converter.class_name for converter in updated_attack.request_converters] == [
                "FirstRequestConverter",
                "SecondRequestConverter",
                "ThirdRequestConverter",
            ]
            assert [converter.class_name for converter in updated_attack.response_converters] == ["ResponseConverter"]
            assert mock_conv_svc.get_converter_objects_for_ids.call_count == 3

    async def test_add_message_resolves_converters_before_writing(self, attack_service, mock_memory) -> None:
        """Test that an unknown converter fails before message or attack writes."""
        ar = make_attack_result(conversation_id="test-id", has_target=False)
        mock_memory.get_attack_results.return_value = [ar]
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["missing"])],
        )

        with patch("pyrit.backend.services.attack_service.get_converter_service") as mock_get_service:
            mock_get_service.return_value.get_converter_objects_for_ids.side_effect = ValueError(
                "Converter instance 'missing' not found"
            )

            with pytest.raises(ValueError, match="Converter instance 'missing' not found"):
                await attack_service.add_message_async(attack_result_id="test-id", request=request)

        mock_memory.add_conversation_to_memory.assert_not_called()
        mock_memory.add_message_pieces_to_memory.assert_not_called()
        mock_memory.update_attack_result_by_id.assert_not_called()

    async def test_add_message_raises_when_attack_not_found_after_update(self, attack_service, mock_memory) -> None:
        """Test that add_message raises ValueError when attack disappears after update."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

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

    async def test_add_message_bumps_timestamp(self, attack_service, mock_memory) -> None:
        """Should bump the timestamp recency column via update_attack_result (no metadata write)."""
        ar = make_attack_result(conversation_id="test-id")
        ar.metadata = {"created_at": "2026-01-01T00:00:00+00:00"}
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        await attack_service.add_message_async(attack_result_id="test-id", request=request)

        mock_memory.update_attack_result_by_id.assert_called_once()
        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["attack_result_id"] == "test-id"
        update_fields = call_kwargs["update_fields"]
        assert isinstance(update_fields["timestamp"], datetime)
        assert "attack_metadata" not in update_fields

    async def test_preconverted_piece_does_not_disable_other_piece_converters(
        self, attack_service, mock_memory
    ) -> None:
        """Test that only the client-preconverted piece is excluded from conversion."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

        mock_converter = MagicMock()
        mock_converter.get_identifier.return_value = ComponentIdentifier(
            class_name="Base64Converter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.attack_service.get_converter_service") as mock_get_conv_svc,
            patch("pyrit.backend.services.attack_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_conv_svc = MagicMock()
            mock_conv_svc.get_converter_objects_for_ids.return_value = [mock_converter]
            mock_get_conv_svc.return_value = mock_conv_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[
                    MessagePieceRequest(original_value="Hello", converted_value="SGVsbG8="),
                    MessagePieceRequest(original_value="World"),
                ],
                send=True,
                target_conversation_id="test-id",
                request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["conv-1"])],
                response_converter_configurations=[ConverterConfigurationRequest(converter_ids=["conv-1"])],
                target_registry_name="test-target",
            )

            await attack_service.add_message_async(attack_result_id="test-id", request=request)

            call_kwargs = mock_normalizer.send_prompt_async.call_args[1]
            request_configurations = call_kwargs["request_converter_configurations"]
            assert len(request_configurations) == 1
            assert request_configurations[0].indexes_to_apply == [1]
            assert len(call_kwargs["response_converter_configurations"]) == 1
            update_call = mock_memory.update_attack_result_by_id.call_args[1]
            assert "atomic_attack_identifier" in update_call["update_fields"]

    def test_preconverted_piece_omits_configuration_with_no_eligible_indexes(self, attack_service) -> None:
        """Test that an empty filtered selector is omitted instead of becoming unrestricted."""
        configuration = ConverterConfiguration(converters=[MagicMock()], indexes_to_apply=[0])

        result = attack_service._exclude_preconverted_piece_indexes(
            configurations=[configuration],
            preconverted_indexes={0},
            piece_count=2,
        )

        assert result == []


# ============================================================================
# Pagination Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestPagination:
    """Tests for pagination in list_attacks."""

    async def test_list_attacks_first_page_forwards_limit_plus_one_and_no_after(
        self, attack_service, mock_memory
    ) -> None:
        """The first page over-fetches one row (limit + 1) and passes no keyset anchor."""
        mock_memory.get_attack_results.return_value = []

        await attack_service.list_attacks_async(limit=20)

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["limit"] == 21
        assert call_kwargs["after"] is None

    async def test_list_attacks_empty_attack_types_match_no_filter_cursor(self, attack_service, mock_memory) -> None:
        """An empty attack-type list has the same query and cursor fingerprint as no filter."""
        backing = _paginated_backing(3)
        mock_memory.get_attack_results.side_effect = _keyset_side_effect(backing)

        first = await attack_service.list_attacks_async(limit=2)
        assert first.pagination.next_cursor is not None
        await attack_service.list_attacks_async(
            attack_types=[],
            limit=2,
            cursor=first.pagination.next_cursor,
        )

        call_kwargs = mock_memory.get_attack_results.call_args.kwargs
        assert call_kwargs["attack_classes"] is None
        assert call_kwargs["after"].attack_result_id == backing[1].attack_result_id

    async def test_list_attacks_decodes_cursor_to_after(self, attack_service, mock_memory) -> None:
        """A cursor is decoded into the memory keyset anchor when its filter fingerprint matches."""
        mock_memory.get_attack_results.return_value = []
        anchor_row = make_attack_result(conversation_id="attack-anchor", attack_result_id=str(uuid.uuid4()))

        await attack_service.list_attacks_async(limit=20, cursor=_cursor_for(anchor_row))

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["after"].attack_result_id == anchor_row.attack_result_id
        assert call_kwargs["limit"] == 21

    async def test_list_attacks_invalid_cursor_defaults_to_first_page(self, attack_service, mock_memory) -> None:
        """A malformed or legacy (offset/attack-result-id) cursor degrades to the first page."""
        mock_memory.get_attack_results.return_value = []

        await attack_service.list_attacks_async(limit=20, cursor="ar-attack-1")

        assert mock_memory.get_attack_results.call_args[1]["after"] is None

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
        assert offset_decoded.timestamp == datetime(2025, 12, 31, 19, 0, 0, tzinfo=timezone.utc)
        assert offset_decoded.timestamp.tzinfo == timezone.utc

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
        mock_memory.get_attack_results.side_effect = _keyset_side_effect(backing)

        result = await attack_service.list_attacks_async(limit=2)

        assert len(result.items) == 2
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor == _cursor_for(backing[1])

    async def test_list_attacks_second_page_via_cursor_is_disjoint(self, attack_service, mock_memory) -> None:
        """Following next_cursor returns the next disjoint page."""
        backing = _paginated_backing(5)
        mock_memory.get_attack_results.side_effect = _keyset_side_effect(backing)

        result = await attack_service.list_attacks_async(limit=2, cursor=_cursor_for(backing[1]))

        assert [item.conversation_id for item in result.items] == ["attack-2", "attack-3"]
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor == _cursor_for(backing[3])

    async def test_list_attacks_last_page_has_no_next_cursor(self, attack_service, mock_memory) -> None:
        """The final page reports has_more False and a null next_cursor."""
        backing = _paginated_backing(5)
        mock_memory.get_attack_results.side_effect = _keyset_side_effect(backing)

        result = await attack_service.list_attacks_async(limit=2, cursor=_cursor_for(backing[3]))

        assert [item.conversation_id for item in result.items] == ["attack-4"]
        assert result.pagination.has_more is False
        assert result.pagination.next_cursor is None

    async def test_list_attacks_prev_cursor_echoes_incoming_cursor(self, attack_service, mock_memory) -> None:
        """prev_cursor echoes the incoming cursor unchanged."""
        mock_memory.get_attack_results.return_value = []
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
        mock_memory.get_attack_results.side_effect = _keyset_side_effect(backing)

        # Page 1 with no filter yields a next_cursor anchored on the last row.
        first = await attack_service.list_attacks_async(limit=2)
        stale_cursor = first.pagination.next_cursor
        assert stale_cursor is not None

        # Replaying it with a different filter set must reset to the first page, not seek.
        result = await attack_service.list_attacks_async(limit=2, cursor=stale_cursor, outcome="success")

        assert mock_memory.get_attack_results.call_args[1]["after"] is None
        assert [item.conversation_id for item in result.items] == ["attack-0", "attack-1"]

    async def test_list_attacks_cursor_with_matching_filters_preserves_anchor(
        self, attack_service, mock_memory
    ) -> None:
        """A cursor replayed with the same filter set applies its encoded keyset anchor."""
        backing = _paginated_backing(5)
        mock_memory.get_attack_results.side_effect = _keyset_side_effect(backing)

        first = await attack_service.list_attacks_async(limit=2, outcome="success")
        next_cursor = first.pagination.next_cursor
        assert next_cursor is not None

        result = await attack_service.list_attacks_async(limit=2, cursor=next_cursor, outcome="success")

        assert mock_memory.get_attack_results.call_args[1]["after"].attack_result_id == backing[1].attack_result_id
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
        """Test that list_attacks uses get_conversation_stats instead of loading full pieces."""
        backing = _paginated_backing(5)
        mock_memory.get_attack_results.side_effect = _keyset_side_effect(backing)

        await attack_service.list_attacks_async(limit=2)

        # get_conversation_stats should be called once (batched), not per-attack
        mock_memory.get_conversation_stats.assert_called_once()
        # get_message_pieces should NOT be called by list_attacks
        mock_memory.get_message_pieces.assert_not_called()


# ============================================================================
# Message Building Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestMessageBuilding:
    """Tests for message translation and building."""

    async def test_get_attack_with_messages_translates_correctly(self, attack_service, mock_memory) -> None:
        """Test that get_conversation_messages translates PyRIT messages to backend format."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

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

        mock_memory.get_conversation_messages.return_value = [msg]

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

        with patch("pyrit.backend.services.attack_service.CentralMemory"):
            service = get_attack_service()
            assert isinstance(service, AttackService)

    def test_get_attack_service_returns_same_instance(self) -> None:
        """Test that get_attack_service returns the same instance."""
        get_attack_service.cache_clear()

        with patch("pyrit.backend.services.attack_service.CentralMemory"):
            service1 = get_attack_service()
            service2 = get_attack_service()
            assert service1 is service2


# ============================================================================
# Persist Base64 Pieces Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestPersistBase64Pieces:
    """Tests for _persist_base64_pieces_async helper."""

    async def test_text_pieces_are_unchanged(self, attack_service) -> None:
        """Text pieces should not be modified."""
        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="text", original_value="hello")],
            send=False,
            target_conversation_id="test-id",
        )
        await AttackService._persist_base64_pieces_async(request)
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
            await AttackService._persist_base64_pieces_async(request)

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
            await AttackService._persist_base64_pieces_async(request)

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
            await AttackService._persist_base64_pieces_async(request)

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
            await AttackService._persist_base64_pieces_async(request)

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
            await AttackService._persist_base64_pieces_async(request)

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
            await AttackService._persist_base64_pieces_async(request)

        factory_mock.assert_called_once_with(
            category="prompt-memory-entries",
            data_type="image_path",
            extension=".png",
        )
        assert request.pieces[0].original_value == "/saved/image.png"

    async def test_http_url_is_kept_as_is(self, attack_service) -> None:
        """HTTPS blob URLs should not be re-persisted."""
        request = AddMessageRequest(
            role="user",
            pieces=[
                MessagePieceRequest(
                    data_type="image_path",
                    original_value="https://myblob.blob.core.windows.net/images/photo.png?sv=2024",
                    mime_type="image/png",
                ),
            ],
            send=False,
            target_conversation_id="test-id",
        )

        await AttackService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == ("https://myblob.blob.core.windows.net/images/photo.png?sv=2024")
        assert request.pieces[0].converted_value == request.pieces[0].original_value

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

        await AttackService._persist_base64_pieces_async(request)

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

            await AttackService._persist_base64_pieces_async(request)

            mock_factory.assert_called_once()
            mock_serializer.save_b64_image_async.assert_called_once_with(data=long_b64)
            assert request.pieces[0].original_value == "/tmp/saved_audio.wav"


# ============================================================================
# Related Conversations Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestGetConversations:
    """Tests for get_conversations_async."""

    async def test_returns_none_when_attack_not_found(self, attack_service, mock_memory):
        """Should return None when the attack doesn't exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.get_conversations_async(attack_result_id="missing")

        assert result is None

    async def test_returns_main_conversation_only(self, attack_service, mock_memory):
        """Should return only the main conversation when no related conversations exist."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_stats.return_value = {
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
        mock_memory.get_attack_results.return_value = [ar]
        path = r"C:\Users\someone\PyRIT\dbdata\prompt-memory-entries\audio\1780010098266691.mp3"
        mock_memory.get_conversation_stats.return_value = {
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

        mock_memory.get_attack_results.return_value = [ar]

        t1 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 1, 9, 30, 0, tzinfo=timezone.utc)  # earlier than t1

        mock_memory.get_conversation_stats.return_value = {
            "attack-1": ConversationStats(message_count=1, last_message_preview="test", created_at=t1),
            "branch-1": ConversationStats(message_count=2, last_message_preview="test", created_at=t2),
            "score-1": ConversationStats(message_count=0),
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

    async def test_returns_none_when_attack_not_found(self, attack_service, mock_memory):
        """Should return None when the attack doesn't exist."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        mock_memory.get_attack_results.return_value = []

        result = await attack_service.create_related_conversation_async(
            attack_result_id="missing",
            request=CreateConversationRequest(),
        )

        assert result is None

    async def test_creates_conversation_and_adds_to_related(self, attack_service, mock_memory):
        """Should create a new conversation and add it to pruned_conversation_ids."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        request = CreateConversationRequest()

        result = await attack_service.create_related_conversation_async(
            attack_result_id="attack-1",
            request=request,
        )

        assert result is not None
        assert result.conversation_id is not None
        assert result.conversation_id != "attack-1"

        # Should have called update_attack_result to persist in DB column
        mock_memory.update_attack_result_by_id.assert_called_once()
        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["attack_result_id"] == "attack-1"
        assert result.conversation_id in call_kwargs["update_fields"]["pruned_conversation_ids"]
        assert isinstance(call_kwargs["update_fields"]["timestamp"], datetime)

    async def test_rejects_source_conversation_from_different_attack(self, attack_service, mock_memory):
        """Should raise ValueError when source_conversation_id doesn't belong to the attack."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]

        request = CreateConversationRequest(source_conversation_id="unrelated-conv", cutoff_index=0)

        with pytest.raises(ValueError, match="not part of attack"):
            await attack_service.create_related_conversation_async(
                attack_result_id="ar-attack-1",
                request=request,
            )


# ============================================================================
# Change Main Conversation Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestUpdateMainConversation:
    """Tests for update_main_conversation_async (promote related conversation to main)."""

    async def test_returns_none_when_attack_not_found(self, attack_service, mock_memory):
        """Should return None when the attack doesn't exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.update_main_conversation_async(
            attack_result_id="missing",
            request=UpdateMainConversationRequest(conversation_id="conv-1"),
        )

        assert result is None

    async def test_noop_when_target_is_already_main(self, attack_service, mock_memory):
        """When target is already the main conversation, return immediately without update."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]

        result = await attack_service.update_main_conversation_async(
            attack_result_id="ar-attack-1",
            request=UpdateMainConversationRequest(conversation_id="attack-1"),
        )

        assert result is not None
        assert result.conversation_id == "attack-1"
        mock_memory.update_attack_result_by_id.assert_not_called()

    async def test_raises_when_conversation_not_part_of_attack(self, attack_service, mock_memory):
        """Should raise ValueError when conversation is not in the attack."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]

        with pytest.raises(ValueError, match="not part of this attack"):
            await attack_service.update_main_conversation_async(
                attack_result_id="ar-attack-1",
                request=UpdateMainConversationRequest(conversation_id="not-related"),
            )

    async def test_swaps_main_conversation(self, attack_service, mock_memory):
        """Changing the main to a related conversation should swap it with the main."""
        from pyrit.models import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(
                conversation_id="branch-1",
                conversation_type=ConversationType.ADVERSARIAL,
                description="Branch 1",
            ),
        }
        mock_memory.get_attack_results.return_value = [ar]

        result = await attack_service.update_main_conversation_async(
            attack_result_id="ar-attack-1",
            request=UpdateMainConversationRequest(conversation_id="branch-1"),
        )

        assert result is not None
        assert result.attack_result_id == "ar-attack-1"
        assert result.conversation_id == "branch-1"

        # Should update via update_attack_result_by_id
        mock_memory.update_attack_result_by_id.assert_called_once()
        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["attack_result_id"] == "ar-attack-1"
        assert call_kwargs["update_fields"]["conversation_id"] == "branch-1"

        # Old main should now be in pruned_conversation_ids (user-visible)
        pruned = call_kwargs["update_fields"]["pruned_conversation_ids"]
        assert "attack-1" in pruned
        assert "branch-1" not in pruned


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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="text", original_value="Hello")],
            send=False,
            target_conversation_id="branch-1",
        )

        now = datetime.now(timezone.utc)
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

    async def test_rejects_unrelated_conversation_id(self, attack_service, mock_memory):
        """Writing to a conversation_id that doesn't belong to the attack should raise ValueError."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(data_type="text", original_value="Hello")],
            send=False,
            target_conversation_id="unrelated-conv",
        )

        with pytest.raises(ValueError, match="not part of attack"):
            await attack_service.add_message_async(attack_result_id="ar-attack-1", request=request)


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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async()

        assert len(result.items) == 1
        assert sorted(result.items[0].related_conversation_ids) == ["branch-1", "branch-2"]

    async def test_list_attacks_no_related_returns_empty_list(self, attack_service, mock_memory):
        """An attack with no related conversations should return empty list."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async()

        assert result.items[0].related_conversation_ids == []

    async def test_create_conversation_increments_count(self, attack_service, mock_memory):
        """Creating a related conversation should add to pruned_conversation_ids."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.create_related_conversation_async(
            attack_result_id="attack-1",
            request=CreateConversationRequest(),
        )

        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        ids = call_kwargs["update_fields"]["pruned_conversation_ids"]
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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.create_related_conversation_async(
            attack_result_id="attack-1",
            request=CreateConversationRequest(),
        )

        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        ids = call_kwargs["update_fields"]["pruned_conversation_ids"]
        assert "conv-existing" in ids
        assert result.conversation_id in ids
        assert len(ids) == 2


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
        mock_memory.get_attack_results.return_value = [ar]

        t_early = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        t_late = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

        mock_memory.get_conversation_stats.return_value = {
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
        mock_memory.get_attack_results.return_value = [ar]

        t = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)

        mock_memory.get_conversation_stats.return_value = {
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
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_stats.return_value = {}  # Both have no stats

        result = await attack_service.get_conversations_async(attack_result_id="attack-1")

        assert result is not None
        # Both empty conversations should have created_at=None
        for conv in result.conversations:
            assert conv.created_at is None


@pytest.mark.usefixtures("patch_central_database")
class TestAttackServiceAdditionalCoverage:
    """Targeted branch coverage tests for attack service helpers and converter merge logic."""

    async def test_create_related_conversation_uses_duplicate_branch(self, attack_service, mock_memory):
        """When source_conversation_id and cutoff_index are provided, duplication path is used."""
        from pyrit.backend.models.attacks import CreateConversationRequest
        from pyrit.models import Conversation

        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]
        expected_target = ComponentIdentifier(class_name="TextTarget", class_module="pyrit.prompt_target")
        mock_memory._get_conversation.return_value = Conversation(
            conversation_id="attack-1", target_identifier=expected_target
        )

        with patch.object(attack_service, "_duplicate_conversation_up_to", return_value="branch-dup") as mock_dup:
            result = await attack_service.create_related_conversation_async(
                attack_result_id="attack-1",
                request=CreateConversationRequest(source_conversation_id="attack-1", cutoff_index=2),
            )

        assert result is not None
        assert result.conversation_id == "branch-dup"
        mock_dup.assert_called_once_with(
            source_conversation_id="attack-1",
            cutoff_index=2,
            target_identifier=expected_target,
        )

    async def test_add_message_merges_converter_identifiers_without_duplicates(self, attack_service, mock_memory):
        """Should merge new converter identifiers with existing attack identifiers by hash."""
        existing_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        duplicate_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        new_converter = ComponentIdentifier(
            class_name="NewConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        ar = make_attack_result(conversation_id="attack-1")
        # Rebuild the atomic_attack_identifier to include an existing converter child
        technique = ar.get_attack_strategy_identifier()
        ar.atomic_attack_identifier = AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                children={
                    "objective_target": technique.get_child("objective_target") if technique else None,
                    "request_converters": [existing_converter],
                },
            ),
        )

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["c-1", "c-2"])],
        )

        update_fields = await _send_message_and_get_update_fields(
            attack_service=attack_service,
            mock_memory=mock_memory,
            attack_result_id="attack-1",
            request=request,
            attack_result=ar,
            converter_identifiers=[duplicate_converter, new_converter],
        )
        # Converters are now stored inside atomic_attack_identifier -> attack_technique -> attack
        atomic_id = update_fields["atomic_attack_identifier"]
        attack_id = atomic_id["children"]["attack_technique"]["children"]["attack"]
        persisted_identifiers = attack_id["children"]["request_converters"]
        persisted_classes = [identifier["class_name"] for identifier in persisted_identifiers]

        assert persisted_classes.count("ExistingConverter") == 1
        assert persisted_classes.count("NewConverter") == 1
        # The removed attack_identifier column should not be written.
        assert "attack_identifier" not in update_fields

    async def test_converter_merge_with_flat_atomic_identifier(self, attack_service, mock_memory):
        """Should merge converters via fallback path when atomic_attack_identifier has no attack_technique child."""
        new_converter = ComponentIdentifier(
            class_name="NewConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        # Build a flat atomic identifier (no attack_technique nesting — legacy shape)
        attack_id = ComponentIdentifier(
            class_name="ManualAttack",
            class_module="pyrit.backend",
            children={
                "objective_target": ComponentIdentifier(class_name="TextTarget", class_module="pyrit.prompt_target"),
            },
        )
        ar = make_attack_result(conversation_id="flat-1")
        ar.atomic_attack_identifier = ComponentIdentifier(
            class_name="AtomicAttack",
            class_module="pyrit.scenario.core.atomic_attack",
            children={"attack": attack_id},
        )

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="flat-1",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["c-1"])],
        )

        update_fields = await _send_message_and_get_update_fields(
            attack_service=attack_service,
            mock_memory=mock_memory,
            attack_result_id="flat-1",
            request=request,
            attack_result=ar,
            converter_identifiers=[new_converter],
        )
        assert "atomic_attack_identifier" in update_fields
        assert "attack_identifier" not in update_fields
        # Flat fallback: converter should be under atomic -> attack -> children
        atomic_id = update_fields["atomic_attack_identifier"]
        attack_child = atomic_id["children"]["attack"]
        persisted_converters = attack_child["children"]["request_converters"]
        assert len(persisted_converters) == 1
        assert persisted_converters[0]["class_name"] == "NewConverter"

    async def test_converter_merge_all_duplicates_does_not_rewrite_identifier(self, attack_service, mock_memory):
        """When every new converter is already present, the identifier is left untouched."""
        existing_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        duplicate_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        ar = make_attack_result(conversation_id="attack-1")
        technique = ar.get_attack_strategy_identifier()
        ar.atomic_attack_identifier = AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                children={
                    "objective_target": technique.get_child("objective_target") if technique else None,
                    "request_converters": [existing_converter],
                },
            ),
        )

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["c-1"])],
        )

        update_fields = await _send_message_and_get_update_fields(
            attack_service=attack_service,
            mock_memory=mock_memory,
            attack_result_id="attack-1",
            request=request,
            attack_result=ar,
            converter_identifiers=[duplicate_converter],
        )
        assert "atomic_attack_identifier" not in update_fields

    async def test_converter_merge_preserves_sibling_children_hash(self, attack_service, mock_memory):
        """Merging a converter must not disturb sibling children (objective_target keeps its hash)."""
        new_converter = ComponentIdentifier(
            class_name="NewConverter",
            class_module="pyrit.converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        ar = make_attack_result(conversation_id="attack-1")
        technique = ar.get_attack_strategy_identifier()
        objective_target = technique.get_child("objective_target") if technique else None
        assert objective_target is not None
        original_target_hash = objective_target.hash

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="test-target",
            request_converter_configurations=[ConverterConfigurationRequest(converter_ids=["c-1"])],
        )

        update_fields = await _send_message_and_get_update_fields(
            attack_service=attack_service,
            mock_memory=mock_memory,
            attack_result_id="attack-1",
            request=request,
            attack_result=ar,
            converter_identifiers=[new_converter],
        )
        rebuilt = AtomicAttackIdentifier.model_validate(update_fields["atomic_attack_identifier"])
        rebuilt_attack = rebuilt.get_child("attack_technique").get_child("attack")
        assert rebuilt_attack.get_child("objective_target").hash == original_target_hash
        merged_converter_classes = [c.class_name for c in rebuilt_attack.get_child_list("request_converters")]
        assert merged_converter_classes == ["NewConverter"]

    def test_duplicate_conversation_up_to_adds_pieces_when_present(self, attack_service, mock_memory):
        """Should duplicate up to cutoff and persist duplicated pieces only when returned."""
        source_messages = [
            make_mock_piece(conversation_id="attack-1", sequence=0),
            make_mock_piece(conversation_id="attack-1", sequence=1),
            make_mock_piece(conversation_id="attack-1", sequence=2),
        ]
        mock_memory.get_conversation_messages.return_value = source_messages
        duplicated_piece = make_mock_piece(conversation_id="branch-1", sequence=0)
        mock_memory.duplicate_messages.return_value = ("branch-1", [duplicated_piece])

        new_id = attack_service._duplicate_conversation_up_to(source_conversation_id="attack-1", cutoff_index=1)

        assert new_id == "branch-1"
        passed_messages = mock_memory.duplicate_messages.call_args[1]["messages"]
        assert [m.sequence for m in passed_messages] == [0, 1]
        mock_memory.add_message_pieces_to_memory.assert_called_once()

    def test_duplicate_conversation_up_to_skips_persist_when_no_duplicated_pieces(self, attack_service, mock_memory):
        """Should not write to memory when duplicate_messages returns no pieces."""
        mock_memory.get_conversation_messages.return_value = [make_mock_piece(conversation_id="attack-1", sequence=0)]
        mock_memory.duplicate_messages.return_value = ("branch-empty", [])

        new_id = attack_service._duplicate_conversation_up_to(source_conversation_id="attack-1", cutoff_index=10)

        assert new_id == "branch-empty"
        mock_memory.add_message_pieces_to_memory.assert_not_called()

    def test_duplicate_conversation_remaps_assistant_to_simulated(self, attack_service, mock_memory):
        """Should remap assistant pieces to simulated_assistant when flag is set."""
        source = make_mock_piece(conversation_id="attack-1", role="assistant", sequence=0)
        mock_memory.get_conversation_messages.return_value = [source]
        dup_piece = make_mock_piece(conversation_id="branch-1", role="assistant", sequence=0)
        mock_memory.duplicate_messages.return_value = ("branch-1", [dup_piece])

        attack_service._duplicate_conversation_up_to(
            source_conversation_id="attack-1", cutoff_index=0, remap_assistant_to_simulated=True
        )

        assert dup_piece.role == "simulated_assistant"

    async def test_store_prepended_messages_noop_when_empty(self, attack_service, mock_memory):
        """Empty prepended list should be a no-op: no conversation row and no piece writes."""
        await attack_service._store_prepended_messages_async(conversation_id="conv-1", prepended=[])

        mock_memory.add_conversation_to_memory.assert_not_called()
        mock_memory.add_message_pieces_to_memory.assert_not_called()


class TestAddMessageGuards:
    """Tests for target-mismatch and operator-mismatch guards in add_message_async."""

    async def test_rejects_mismatched_target(self, attack_service, mock_memory) -> None:
        """Should raise ValueError when request target differs from attack target."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        # Create a mock target with a different class_name
        wrong_target = MagicMock()
        wrong_target.get_identifier.return_value = ComponentIdentifier(
            class_name="DifferentTarget",
            class_module="pyrit.prompt_target",
        )

        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc:
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = wrong_target
            mock_get_target_svc.return_value = mock_target_svc

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="wrong-target",
            )

            with pytest.raises(ValueError, match="Target mismatch"):
                await attack_service.add_message_async(attack_result_id="test-id", request=request)

    async def test_allows_matching_target(self, attack_service, mock_memory) -> None:
        """Should NOT raise when request target matches attack target."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation_messages.return_value = []

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.attack_service.PromptNormalizer") as mock_normalizer_cls,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                target_registry_name="test-target",
            )

            result = await attack_service.add_message_async(attack_result_id="test-id", request=request)
            assert result.attack is not None

    def test_allows_matching_round_robin_target(self, attack_service) -> None:
        """Equivalent composite identifiers should pass target validation."""
        stored_target_id = _make_round_robin_identifier()
        request_target = MagicMock()
        request_target.get_identifier.return_value = _make_round_robin_identifier()
        attack_identifier = ComponentIdentifier(
            class_name="ManualAttack",
            class_module="pyrit.executor.attack",
            children={"objective_target": stored_target_id},
        )
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="round-robin",
        )

        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc:
            mock_get_target_svc.return_value.get_target_object.return_value = request_target

            attack_service._validate_target_match(attack_identifier=attack_identifier, request=request)

    @pytest.mark.parametrize(
        ("second_model_name", "weights"),
        [
            ("different-model", (1, 1)),
            ("e2e-dummy-model", (2, 1)),
        ],
        ids=["inner-target", "weights"],
    )
    def test_rejects_incompatible_round_robin_target(
        self,
        attack_service,
        second_model_name: str,
        weights: tuple[int, int],
    ) -> None:
        """Composite differences should be rejected despite identical nullable root fields."""
        stored_target_id = _make_round_robin_identifier()
        request_target = MagicMock()
        request_target.get_identifier.return_value = _make_round_robin_identifier(
            second_model_name=second_model_name,
            weights=weights,
        )
        attack_identifier = ComponentIdentifier(
            class_name="ManualAttack",
            class_module="pyrit.executor.attack",
            children={"objective_target": stored_target_id},
        )
        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=True,
            target_registry_name="round-robin",
        )

        with patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc:
            mock_get_target_svc.return_value.get_target_object.return_value = request_target

            with pytest.raises(ValueError, match="Target mismatch"):
                attack_service._validate_target_match(attack_identifier=attack_identifier, request=request)

    async def test_rejects_mismatched_operator(self, attack_service, mock_memory) -> None:
        """Should raise ValueError when request operator differs from attack operator."""
        ar = make_attack_result(conversation_id="test-id")
        ar.labels["operator"] = "alice"
        mock_memory.get_attack_results.return_value = [ar]

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
            labels={"operator": "bob"},
        )

        with pytest.raises(ValueError, match="Operator mismatch"):
            await attack_service.add_message_async(attack_result_id="test-id", request=request)

    async def test_allows_matching_operator(self, attack_service, mock_memory) -> None:
        """Should NOT raise when request operator matches attack operator."""
        ar = make_attack_result(conversation_id="test-id")
        ar.labels["operator"] = "alice"
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation_messages.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
            labels={"operator": "alice"},
        )

        result = await attack_service.add_message_async(attack_result_id="test-id", request=request)
        assert result.attack is not None


class TestResolveVideoRemixMetadata:
    """Tests for _resolve_video_remix_metadata."""

    def test_resolves_video_id_from_original_piece(self, attack_service, mock_memory):
        """When a video_path piece has original_prompt_id, resolve video_id onto text piece."""
        original_piece = MagicMock()
        original_piece.prompt_metadata = {"video_id": "vid-abc-123"}
        mock_memory.get_message_pieces.return_value = [original_piece]

        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[
                MessagePieceRequest(original_value="remix this video", data_type="text"),
                MessagePieceRequest(
                    original_value="/path/to/video.mp4",
                    data_type="video_path",
                    original_prompt_id="piece-id-1",
                ),
            ],
        )

        attack_service._resolve_video_remix_metadata(request)

        assert request.pieces[0].prompt_metadata == {"video_id": "vid-abc-123"}
        assert request.pieces[1].prompt_metadata == {"video_id": "vid-abc-123"}

    def test_no_op_without_video_pieces(self, attack_service):
        """Should do nothing when there are no video_path pieces."""
        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[MessagePieceRequest(original_value="just text", data_type="text")],
        )

        attack_service._resolve_video_remix_metadata(request)

        assert request.pieces[0].prompt_metadata is None

    def test_no_op_when_video_id_already_set(self, attack_service, mock_memory):
        """Should not overwrite existing video_id on text piece."""
        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[
                MessagePieceRequest(
                    original_value="remix",
                    data_type="text",
                    prompt_metadata={"video_id": "existing-id"},
                ),
                MessagePieceRequest(
                    original_value="/path/to/video.mp4",
                    data_type="video_path",
                    original_prompt_id="piece-id-1",
                ),
            ],
        )

        attack_service._resolve_video_remix_metadata(request)

        assert request.pieces[0].prompt_metadata == {"video_id": "existing-id"}
        mock_memory.get_message_pieces.assert_not_called()

    def test_no_op_without_original_prompt_id(self, attack_service, mock_memory):
        """Should not crash when video_path piece has no original_prompt_id."""
        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[
                MessagePieceRequest(original_value="remix", data_type="text"),
                MessagePieceRequest(original_value="/path/to/video.mp4", data_type="video_path"),
            ],
        )

        attack_service._resolve_video_remix_metadata(request)

        assert request.pieces[0].prompt_metadata is None
        mock_memory.get_message_pieces.assert_not_called()

    def test_no_op_when_original_piece_has_no_video_id(self, attack_service, mock_memory):
        """Should not set metadata when original piece has no video_id."""
        original_piece = MagicMock()
        original_piece.prompt_metadata = {"other_key": "value"}
        mock_memory.get_message_pieces.return_value = [original_piece]

        request = AddMessageRequest(
            role="user",
            target_conversation_id="conv-1",
            pieces=[
                MessagePieceRequest(original_value="remix", data_type="text"),
                MessagePieceRequest(
                    original_value="/path/to/video.mp4",
                    data_type="video_path",
                    original_prompt_id="piece-id-1",
                ),
            ],
        )

        attack_service._resolve_video_remix_metadata(request)

        assert request.pieces[0].prompt_metadata is None
