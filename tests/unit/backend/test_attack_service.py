# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for attack service.

The attack service uses PyRIT memory with AttackResult as the source of truth.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.models.attacks import (
    AddMessageRequest,
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
from pyrit.identifiers import ComponentIdentifier
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.models import AttackOutcome, AttackResult
from pyrit.models.conversation_stats import ConversationStats


@pytest.fixture
def mock_memory():
    """Create a mock memory instance."""
    memory = MagicMock()
    memory.get_attack_results.return_value = []
    memory.get_conversation.return_value = []
    memory.get_message_pieces.return_value = []
    memory.get_conversation_stats.return_value = {}

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
    created_at: datetime = None,
    updated_at: datetime = None,
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
        atomic_attack_identifier=build_atomic_attack_identifier(
            attack_identifier=ComponentIdentifier(
                class_name=name,
                class_module="pyrit.backend",
                children={"objective_target": target_identifier} if target_identifier else {},
            ),
        ),
        outcome=outcome,
        attack_result_id=effective_ar_id,
        metadata={
            "created_at": created.isoformat(),
            "updated_at": updated.isoformat(),
        },
    )


def _make_matching_target_mock() -> MagicMock:
    """Create a mock target object whose get_identifier() matches make_attack_result's default target."""
    mock_target = MagicMock()
    mock_target.get_identifier.return_value = ComponentIdentifier(
        class_name="TextTarget",
        class_module="pyrit.prompt_target",
    )
    return mock_target


def make_mock_piece(
    *,
    conversation_id: str,
    role: str = "user",
    sequence: int = 0,
    original_value: str = "test",
    converted_value: str = "test",
    timestamp: datetime = None,
):
    """Create a mock message piece."""
    piece = MagicMock()
    piece.id = "piece-id"
    piece.conversation_id = conversation_id
    piece.role = role
    piece.get_role_for_storage.return_value = role
    piece.sequence = sequence
    piece.original_value = original_value
    piece.converted_value = converted_value
    piece.converted_value_data_type = "text"
    piece.original_value_data_type = "text"
    piece.response_error = "none"
    piece.timestamp = timestamp or datetime.now(timezone.utc)
    piece.scores = []
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

    @pytest.mark.asyncio
    async def test_list_attacks_returns_empty_when_no_attacks(self, attack_service, mock_memory) -> None:
        """Test that list_attacks returns empty list when no AttackResults exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.list_attacks_async()

        assert result.items == []
        assert result.pagination.has_more is False

    @pytest.mark.asyncio
    async def test_list_attacks_returns_attacks(self, attack_service, mock_memory) -> None:
        """Test that list_attacks returns attacks from AttackResult records."""
        ar = make_attack_result()
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async()

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-1"
        assert result.items[0].attack_type == "Test Attack"

    @pytest.mark.asyncio
    async def test_list_attacks_filters_by_attack_type_exact(self, attack_service, mock_memory) -> None:
        """Test that list_attacks passes attack_type to memory layer."""
        ar1 = make_attack_result(conversation_id="attack-1", name="CrescendoAttack")
        mock_memory.get_attack_results.return_value = [ar1]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async(attack_type="CrescendoAttack")

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-1"
        # Verify attack_type was forwarded to the memory layer as attack_class
        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["attack_class"] == "CrescendoAttack"

    @pytest.mark.asyncio
    async def test_list_attacks_attack_type_passed_to_memory(self, attack_service, mock_memory) -> None:
        """Test that attack_type is forwarded to memory as attack_class for DB-level filtering."""
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(attack_type="Crescendo")

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["attack_class"] == "Crescendo"

    @pytest.mark.asyncio
    async def test_list_attacks_filters_by_no_converters(self, attack_service, mock_memory) -> None:
        """Test that converter_types=[] is forwarded to memory for DB-level filtering."""
        mock_memory.get_attack_results.return_value = []
        mock_memory.get_message_pieces.return_value = []

        await attack_service.list_attacks_async(converter_types=[])

        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["converter_classes"] == []

    @pytest.mark.asyncio
    async def test_list_attacks_filters_by_converter_types_and_logic(self, attack_service, mock_memory) -> None:
        """Test that list_attacks passes converter_types to memory layer."""
        ar1 = make_attack_result(conversation_id="attack-1", name="Attack One")
        ar1.atomic_attack_identifier = build_atomic_attack_identifier(
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
        # Verify converter_types was forwarded to the memory layer
        call_kwargs = mock_memory.get_attack_results.call_args[1]
        assert call_kwargs["converter_classes"] == ["Base64Converter", "ROT13Converter"]

    @pytest.mark.asyncio
    async def test_list_attacks_filters_by_min_turns(self, attack_service, mock_memory) -> None:
        """Test that list_attacks filters by minimum executed turns."""
        ar1 = make_attack_result(conversation_id="attack-1")
        ar1.executed_turns = 5
        ar2 = make_attack_result(conversation_id="attack-2")
        ar2.executed_turns = 2
        mock_memory.get_attack_results.return_value = [ar1, ar2]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async(min_turns=3)

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-1"

    @pytest.mark.asyncio
    async def test_list_attacks_filters_by_max_turns(self, attack_service, mock_memory) -> None:
        """Test that list_attacks filters by maximum executed turns."""
        ar1 = make_attack_result(conversation_id="attack-1")
        ar1.executed_turns = 5
        ar2 = make_attack_result(conversation_id="attack-2")
        ar2.executed_turns = 2
        mock_memory.get_attack_results.return_value = [ar1, ar2]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async(max_turns=3)

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-2"

    @pytest.mark.asyncio
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
        assert result.items[0].labels == {"env": "prod", "team": "red"}

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_list_attacks_combined_min_and_max_turns(self, attack_service, mock_memory) -> None:
        """Test that list_attacks filters by both min_turns and max_turns together."""
        ar1 = make_attack_result(conversation_id="attack-1")
        ar1.executed_turns = 1
        ar2 = make_attack_result(conversation_id="attack-2")
        ar2.executed_turns = 3
        ar3 = make_attack_result(conversation_id="attack-3")
        ar3.executed_turns = 7
        mock_memory.get_attack_results.return_value = [ar1, ar2, ar3]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async(min_turns=2, max_turns=5)

        assert len(result.items) == 1
        assert result.items[0].conversation_id == "attack-2"


# ============================================================================
# Attack Options Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestAttackOptions:
    """Tests for get_attack_options_async method."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_attacks(self, attack_service, mock_memory) -> None:
        """Test that attack options returns empty list when no attacks exist."""
        mock_memory.get_unique_attack_class_names.return_value = []

        result = await attack_service.get_attack_options_async()

        assert result == []
        mock_memory.get_unique_attack_class_names.assert_called_once()

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_attacks(self, attack_service, mock_memory) -> None:
        """Test that converter options returns empty list when no attacks exist."""
        mock_memory.get_unique_converter_class_names.return_value = []

        result = await attack_service.get_converter_options_async()

        assert result == []
        mock_memory.get_unique_converter_class_names.assert_called_once()

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_get_attack_returns_none_for_nonexistent(self, attack_service, mock_memory) -> None:
        """Test that get_attack returns None when AttackResult doesn't exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.get_attack_async(attack_result_id="nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_attack_returns_attack_details(self, attack_service, mock_memory) -> None:
        """Test that get_attack returns attack details from AttackResult."""
        ar = make_attack_result(
            conversation_id="test-id",
            name="My Attack",
        )
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation.return_value = []

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

    @pytest.mark.asyncio
    async def test_get_conversation_messages_returns_none_for_nonexistent(self, attack_service, mock_memory) -> None:
        """Test that get_conversation_messages returns None when attack doesn't exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="nonexistent", conversation_id="any-id"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_conversation_messages_returns_messages(self, attack_service, mock_memory) -> None:
        """Test that get_conversation_messages returns messages for existing attack."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation.return_value = []

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="test-id", conversation_id="test-id"
        )

        assert result is not None
        assert result.conversation_id == "test-id"
        assert result.messages == []

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_create_attack_does_not_store_labels_in_metadata(self, attack_service, mock_memory) -> None:
        """Test that labels are not stored in attack metadata (they live on pieces)."""
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

    @pytest.mark.asyncio
    async def test_create_attack_stamps_labels_on_prepended_pieces(self, attack_service, mock_memory) -> None:
        """Test that labels are forwarded to prepended message pieces."""
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
                    pieces=[MessagePieceRequest(original_value="Be helpful.")],
                )
            ]

            await attack_service.create_attack_async(
                request=CreateAttackRequest(
                    target_registry_name="target-1",
                    labels={"env": "prod"},
                    prepended_conversation=prepended,
                )
            )

            stored_piece = mock_memory.add_message_pieces_to_memory.call_args[1]["message_pieces"][0]
            assert stored_piece.labels == {"env": "prod", "source": "gui"}

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

            prepended = [
                PrependedMessageRequest(
                    role="system",
                    pieces=[MessagePieceRequest(original_value="Be helpful.")],
                )
            ]

            await attack_service.create_attack_async(
                request=CreateAttackRequest(
                    target_registry_name="target-1",
                    labels={"source": "api-test"},
                    prepended_conversation=prepended,
                )
            )

            stored_piece = mock_memory.add_message_pieces_to_memory.call_args[1]["message_pieces"][0]
            assert stored_piece.labels["source"] == "api-test"  # not overwritten to "gui"

    @pytest.mark.asyncio
    async def test_create_attack_default_name(self, attack_service, mock_memory) -> None:
        """Test that request.name=None uses default class_name and objective."""
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
            assert stored_ar.objective == "Manual attack via GUI"
            assert stored_ar.attack_identifier.class_name == "ManualAttack"


# ============================================================================
# Update Attack Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestUpdateAttack:
    """Tests for update_attack method."""

    @pytest.mark.asyncio
    async def test_update_attack_returns_none_for_nonexistent(self, attack_service, mock_memory) -> None:
        """Test that update_attack returns None for nonexistent attack."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.update_attack_async(
            attack_result_id="nonexistent", request=UpdateAttackRequest(outcome="success")
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_update_attack_updates_outcome_success(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'success' to AttackOutcome.SUCCESS."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="success")
        )

        mock_memory.update_attack_result_by_id.assert_called_once()
        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["attack_result_id"] == "test-id"
        assert call_kwargs["update_fields"]["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_update_attack_updates_outcome_failure(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'failure' to AttackOutcome.FAILURE."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="failure")
        )

        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["update_fields"]["outcome"] == "failure"

    @pytest.mark.asyncio
    async def test_update_attack_updates_outcome_undetermined(self, attack_service, mock_memory) -> None:
        """Test that update_attack maps 'undetermined' to AttackOutcome.UNDETERMINED."""
        ar = make_attack_result(conversation_id="test-id", outcome=AttackOutcome.SUCCESS)
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="undetermined")
        )

        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["update_fields"]["outcome"] == "undetermined"

    @pytest.mark.asyncio
    async def test_update_attack_refreshes_updated_at(self, attack_service, mock_memory) -> None:
        """Test that update_attack refreshes the updated_at metadata."""
        old_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ar = make_attack_result(conversation_id="test-id", updated_at=old_time)
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_conversation.return_value = []

        await attack_service.update_attack_async(
            attack_result_id="test-id", request=UpdateAttackRequest(outcome="success")
        )

        call_kwargs = mock_memory.update_attack_result_by_id.call_args[1]
        assert call_kwargs["update_fields"]["attack_metadata"]["updated_at"] != old_time.isoformat()


# ============================================================================
# Add Message Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestAddMessage:
    """Tests for add_message method."""

    @pytest.mark.asyncio
    async def test_add_message_raises_for_nonexistent_attack(self, attack_service, mock_memory) -> None:
        """Test that add_message raises ValueError for nonexistent attack."""
        mock_memory.get_attack_results.return_value = []

        request = AddMessageRequest(
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
        )

        with pytest.raises(ValueError, match="not found"):
            await attack_service.add_message_async(attack_result_id="nonexistent", request=request)

    @pytest.mark.asyncio
    async def test_add_message_without_send_stamps_labels_on_pieces(self, attack_service, mock_memory) -> None:
        """Test that add_message (send=False) inherits labels from existing pieces."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

        existing_piece = make_mock_piece(conversation_id="test-id")
        existing_piece.labels = {"env": "prod"}
        mock_memory.get_message_pieces.return_value = [existing_piece]
        mock_memory.get_conversation.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        result = await attack_service.add_message_async(attack_result_id="test-id", request=request)

        stored_piece = mock_memory.add_message_pieces_to_memory.call_args[1]["message_pieces"][0]
        assert stored_piece.labels == {"env": "prod"}
        assert result.attack is not None

    @pytest.mark.asyncio
    async def test_add_message_with_send_passes_labels_to_normalizer(self, attack_service, mock_memory) -> None:
        """Test that add_message (send=True) inherits labels from existing pieces."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

        existing_piece = make_mock_piece(conversation_id="test-id")
        existing_piece.labels = {"env": "staging"}
        mock_memory.get_message_pieces.return_value = [existing_piece]
        mock_memory.get_conversation.return_value = []

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

            await attack_service.add_message_async(attack_result_id="test-id", request=request)

            call_kwargs = mock_normalizer.send_prompt_async.call_args[1]
            assert call_kwargs["labels"] == {"env": "staging"}

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_add_message_send_false_without_registry_name_succeeds(self, attack_service, mock_memory) -> None:
        """Test that add_message with send=False does not require target_registry_name."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

        request = AddMessageRequest(
            role="system",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        result = await attack_service.add_message_async(attack_result_id="test-id", request=request)
        assert result.attack is not None

    @pytest.mark.asyncio
    async def test_add_message_with_send_sends_via_normalizer(self, attack_service, mock_memory) -> None:
        """Test that add_message with send=True sends message via normalizer."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_add_message_with_converter_ids_gets_converters(self, attack_service, mock_memory) -> None:
        """Test that add_message with converter_ids gets converters from service."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

        with (
            patch("pyrit.backend.services.attack_service.get_target_service") as mock_get_target_svc,
            patch("pyrit.backend.services.attack_service.get_converter_service") as mock_get_conv_svc,
            patch("pyrit.backend.services.attack_service.PromptNormalizer") as mock_normalizer_cls,
            patch("pyrit.backend.services.attack_service.PromptConverterConfiguration") as mock_config,
        ):
            mock_target_svc = MagicMock()
            mock_target_svc.get_target_object.return_value = _make_matching_target_mock()
            mock_get_target_svc.return_value = mock_target_svc

            mock_conv_svc = MagicMock()
            mock_converter = MagicMock()
            mock_converter.get_identifier.return_value = ComponentIdentifier(
                class_name="TestConverter",
                class_module="test_module",
                params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
            )
            mock_conv_svc.get_converter_objects_for_ids.return_value = [mock_converter]
            mock_get_conv_svc.return_value = mock_conv_svc

            mock_config.from_converters.return_value = [MagicMock()]

            mock_normalizer = MagicMock()
            mock_normalizer.send_prompt_async = AsyncMock()
            mock_normalizer_cls.return_value = mock_normalizer

            request = AddMessageRequest(
                pieces=[MessagePieceRequest(original_value="Hello")],
                target_conversation_id="test-id",
                send=True,
                converter_ids=["conv-1"],
                target_registry_name="test-target",
            )

            await attack_service.add_message_async(attack_result_id="test-id", request=request)

            mock_conv_svc.get_converter_objects_for_ids.assert_any_call(converter_ids=["conv-1"])

    @pytest.mark.asyncio
    async def test_add_message_raises_when_attack_not_found_after_update(self, attack_service, mock_memory) -> None:
        """Test that add_message raises ValueError when attack disappears after update."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

        request = AddMessageRequest(
            role="system",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        with patch.object(attack_service, "get_attack_async", new=AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="not found after update"):
                await attack_service.add_message_async(attack_result_id="test-id", request=request)

    @pytest.mark.asyncio
    async def test_add_message_raises_when_messages_not_found_after_update(self, attack_service, mock_memory) -> None:
        """Test that add_message raises ValueError when messages disappear after update."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

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

    @pytest.mark.asyncio
    async def test_add_message_persists_updated_at_timestamp(self, attack_service, mock_memory) -> None:
        """Should persist updated_at in attack_metadata via update_attack_result."""
        ar = make_attack_result(conversation_id="test-id")
        ar.metadata = {"created_at": "2026-01-01T00:00:00+00:00"}
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

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
        persisted_metadata = call_kwargs["update_fields"]["attack_metadata"]
        assert "updated_at" in persisted_metadata
        assert persisted_metadata["created_at"] == "2026-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_converter_ids_propagate_even_when_preconverted(self, attack_service, mock_memory) -> None:
        """Test that converter identifiers propagate to attack_identifier even when pieces are preconverted."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

        mock_converter = MagicMock()
        mock_converter.get_identifier.return_value = ComponentIdentifier(
            class_name="Base64Converter",
            class_module="pyrit.prompt_converter",
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
                pieces=[MessagePieceRequest(original_value="Hello", converted_value="SGVsbG8=")],
                send=True,
                target_conversation_id="test-id",
                converter_ids=["conv-1"],
                target_registry_name="test-target",
            )

            await attack_service.add_message_async(attack_result_id="test-id", request=request)

            # Converter service IS called to resolve identifiers for the attack_identifier
            mock_get_conv_svc.assert_called()
            # Normalizer should still get empty converter configs since pieces are preconverted
            call_kwargs = mock_normalizer.send_prompt_async.call_args[1]
            assert call_kwargs["request_converter_configurations"] == []
            # attack_identifier should be updated with converter identifiers
            update_call = mock_memory.update_attack_result_by_id.call_args[1]
            assert "attack_identifier" in update_call["update_fields"]

    @pytest.mark.asyncio
    async def test_add_message_no_existing_pieces_uses_request_labels(self, attack_service, mock_memory) -> None:
        """Test that add_message with no existing pieces falls back to request.labels."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []  # No existing pieces
        mock_memory.get_conversation.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
            labels={"env": "prod", "source": "gui"},
        )

        result = await attack_service.add_message_async(attack_result_id="test-id", request=request)

        stored_piece = mock_memory.add_message_pieces_to_memory.call_args[1]["message_pieces"][0]
        assert stored_piece.labels == {"env": "prod", "source": "gui"}
        assert result.attack is not None

    @pytest.mark.asyncio
    async def test_add_message_no_existing_pieces_uses_request_labels_as_is(self, attack_service, mock_memory) -> None:
        """Test that add_message passes request labels through as-is when stamping new pieces."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
            labels={"operator": "alice", "operation": "red"},
        )

        await attack_service.add_message_async(attack_result_id="test-id", request=request)

        stored_piece = mock_memory.add_message_pieces_to_memory.call_args[1]["message_pieces"][0]
        assert stored_piece.labels == {"operator": "alice", "operation": "red"}

    @pytest.mark.asyncio
    async def test_add_message_no_existing_pieces_no_request_labels(self, attack_service, mock_memory) -> None:
        """Test that add_message with no existing pieces and no request.labels passes None."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []  # No existing pieces
        mock_memory.get_conversation.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
        )

        result = await attack_service.add_message_async(attack_result_id="test-id", request=request)

        stored_piece = mock_memory.add_message_pieces_to_memory.call_args[1]["message_pieces"][0]
        assert stored_piece.labels is None or stored_piece.labels == {}
        assert result.attack is not None


# ============================================================================
# Pagination Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestPagination:
    """Tests for pagination in list_attacks."""

    @pytest.mark.asyncio
    async def test_list_attacks_with_cursor_paginates(self, attack_service, mock_memory) -> None:
        """Test that list_attacks with cursor starts from the right position."""
        ar1 = make_attack_result(conversation_id="attack-1")
        ar2 = make_attack_result(conversation_id="attack-2")
        ar3 = make_attack_result(conversation_id="attack-3")
        mock_memory.get_attack_results.return_value = [ar1, ar2, ar3]
        mock_memory.get_message_pieces.return_value = []

        # Get first page
        result = await attack_service.list_attacks_async(limit=2)
        # Results are sorted by updated_at desc, so order may vary
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_list_attacks_has_more_flag(self, attack_service, mock_memory) -> None:
        """Test that list_attacks sets has_more flag correctly."""
        ar1 = make_attack_result(conversation_id="attack-1")
        ar2 = make_attack_result(conversation_id="attack-2")
        ar3 = make_attack_result(conversation_id="attack-3")
        mock_memory.get_attack_results.return_value = [ar1, ar2, ar3]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async(limit=2)

        assert result.pagination.has_more is True
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_list_attacks_cursor_skips_to_correct_position(self, attack_service, mock_memory) -> None:
        """Test that list_attacks with cursor skips items before cursor."""
        ar1 = make_attack_result(
            conversation_id="attack-1",
            updated_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
        )
        ar2 = make_attack_result(
            conversation_id="attack-2",
            updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        ar3 = make_attack_result(
            conversation_id="attack-3",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        mock_memory.get_attack_results.return_value = [ar1, ar2, ar3]
        mock_memory.get_message_pieces.return_value = []

        # Cursor = ar-attack-1 should skip attack-1 and return from attack-2 onward
        result = await attack_service.list_attacks_async(cursor="ar-attack-1", limit=10)

        attack_ids = [item.conversation_id for item in result.items]
        assert "attack-1" not in attack_ids
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_list_attacks_uses_conversation_stats_not_pieces(self, attack_service, mock_memory) -> None:
        """Test that list_attacks uses get_conversation_stats instead of loading full pieces."""
        attacks = [make_attack_result(conversation_id=f"attack-{i}") for i in range(5)]
        mock_memory.get_attack_results.return_value = attacks

        await attack_service.list_attacks_async(limit=2)

        # get_conversation_stats should be called once (batched), not per-attack
        mock_memory.get_conversation_stats.assert_called_once()
        # get_message_pieces should NOT be called by list_attacks
        mock_memory.get_message_pieces.assert_not_called()

    @pytest.mark.asyncio
    async def test_pagination_cursor_not_found_returns_from_start(self, attack_service, mock_memory) -> None:
        """Test that a stale/invalid cursor defaults to returning from the beginning."""
        ar1 = make_attack_result(
            conversation_id="attack-1",
            updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        ar2 = make_attack_result(
            conversation_id="attack-2",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        mock_memory.get_attack_results.return_value = [ar1, ar2]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async(cursor="nonexistent-cursor", limit=10)

        # Should return all items (from beginning) since cursor wasn't found
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_pagination_cursor_at_last_item_returns_empty(self, attack_service, mock_memory) -> None:
        """Test that cursor pointing to the last item returns empty page with has_more=False."""
        ar1 = make_attack_result(
            conversation_id="attack-1",
            updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        ar2 = make_attack_result(
            conversation_id="attack-2",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        mock_memory.get_attack_results.return_value = [ar1, ar2]
        mock_memory.get_message_pieces.return_value = []

        # Cursor = last sorted item (attack-2 has the oldest updated_at, so it's last)
        result = await attack_service.list_attacks_async(cursor="ar-attack-2", limit=10)

        assert len(result.items) == 0
        assert result.pagination.has_more is False


# ============================================================================
# Message Building Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestMessageBuilding:
    """Tests for message translation and building."""

    @pytest.mark.asyncio
    async def test_get_attack_with_messages_translates_correctly(self, attack_service, mock_memory) -> None:
        """Test that get_conversation_messages translates PyRIT messages to backend format."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

        # Create mock message with pieces
        mock_piece = MagicMock()
        mock_piece.id = "piece-1"
        mock_piece.converted_value_data_type = "text"
        mock_piece.original_value_data_type = "text"
        mock_piece.original_value = "Hello"
        mock_piece.converted_value = "Hello"
        mock_piece.response_error = None
        mock_piece.sequence = 0
        mock_piece.role = "user"
        mock_piece.get_role_for_storage.return_value = "user"
        mock_piece.timestamp = datetime.now(timezone.utc)
        mock_piece.scores = None

        mock_msg = MagicMock()
        mock_msg.message_pieces = [mock_piece]

        mock_memory.get_conversation.return_value = [mock_msg]

        result = await attack_service.get_conversation_messages_async(
            attack_result_id="test-id", conversation_id="test-id"
        )

        assert result is not None
        assert len(result.messages) == 1
        assert result.messages[0].role == "user"
        assert len(result.messages[0].pieces) == 1
        assert result.messages[0].pieces[0].original_value == "Hello"


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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
        mock_serializer.save_b64_image = AsyncMock()
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
        mock_serializer.save_b64_image.assert_awaited_once_with(data="aW1hZ2VkYXRh")
        assert request.pieces[0].original_value == "/saved/image.png"

    @pytest.mark.asyncio
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
        mock_serializer.save_b64_image = AsyncMock()
        mock_serializer.value = "/saved/photo.jpg"

        with patch(
            "pyrit.backend.services.attack_service.data_serializer_factory",
            return_value=mock_serializer,
        ):
            await AttackService._persist_base64_pieces_async(request)

        assert request.pieces[0].original_value == "describe this"
        assert request.pieces[1].original_value == "/saved/photo.jpg"

    @pytest.mark.asyncio
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
        mock_serializer.save_b64_image = AsyncMock()
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

    @pytest.mark.asyncio
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
        mock_serializer.save_b64_image = AsyncMock()
        mock_serializer.value = "/saved/image.png"

        with patch(
            "pyrit.backend.services.attack_service.data_serializer_factory",
            return_value=mock_serializer,
        ):
            await AttackService._persist_base64_pieces_async(request)

        # Should receive only the base64 payload, not the data URI prefix
        mock_serializer.save_b64_image.assert_awaited_once_with(data="aW1hZ2VkYXRh")
        assert request.pieces[0].original_value == "/saved/image.png"

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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


# ============================================================================
# Related Conversations Tests
# ============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestGetConversations:
    """Tests for get_conversations_async."""

    @pytest.mark.asyncio
    async def test_returns_none_when_attack_not_found(self, attack_service, mock_memory):
        """Should return None when the attack doesn't exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.get_conversations_async(attack_result_id="missing")

        assert result is None

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_returns_main_and_related_conversations(self, attack_service, mock_memory):
        """Should return main and PRUNED conversations sorted by timestamp."""
        from pyrit.models.conversation_reference import ConversationReference, ConversationType

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

    @pytest.mark.asyncio
    async def test_returns_none_when_attack_not_found(self, attack_service, mock_memory):
        """Should return None when the attack doesn't exist."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        mock_memory.get_attack_results.return_value = []

        result = await attack_service.create_related_conversation_async(
            attack_result_id="missing",
            request=CreateConversationRequest(),
        )

        assert result is None

    @pytest.mark.asyncio
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
        assert "updated_at" in call_kwargs["update_fields"]["attack_metadata"]

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_returns_none_when_attack_not_found(self, attack_service, mock_memory):
        """Should return None when the attack doesn't exist."""
        mock_memory.get_attack_results.return_value = []

        result = await attack_service.update_main_conversation_async(
            attack_result_id="missing",
            request=UpdateMainConversationRequest(conversation_id="conv-1"),
        )

        assert result is None

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_raises_when_conversation_not_part_of_attack(self, attack_service, mock_memory):
        """Should raise ValueError when conversation is not in the attack."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]

        with pytest.raises(ValueError, match="not part of this attack"):
            await attack_service.update_main_conversation_async(
                attack_result_id="ar-attack-1",
                request=UpdateMainConversationRequest(conversation_id="not-related"),
            )

    @pytest.mark.asyncio
    async def test_swaps_main_conversation(self, attack_service, mock_memory):
        """Changing the main to a related conversation should swap it with the main."""
        from pyrit.models.conversation_reference import ConversationReference, ConversationType

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

    @pytest.mark.asyncio
    async def test_stores_message_in_target_conversation(self, attack_service, mock_memory):
        """When target_conversation_id is set, messages should go to that conversation."""
        from pyrit.backend.models.attacks import AttackSummary, ConversationMessagesResponse
        from pyrit.models.conversation_reference import ConversationReference, ConversationType

        ar = make_attack_result(conversation_id="attack-1")
        ar.related_conversations = {
            ConversationReference(conversation_id="branch-1", conversation_type=ConversationType.PRUNED),
        }
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

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
            attack_type="ManualAttack",
            converters=[],
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_list_attacks_includes_related_conversation_ids(self, attack_service, mock_memory):
        """Attacks with related conversations should expose them in the summary."""
        from pyrit.models.conversation_reference import ConversationReference, ConversationType

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

    @pytest.mark.asyncio
    async def test_list_attacks_no_related_returns_empty_list(self, attack_service, mock_memory):
        """An attack with no related conversations should return empty list."""
        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        result = await attack_service.list_attacks_async()

        assert result.items[0].related_conversation_ids == []

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_create_second_conversation_preserves_first(self, attack_service, mock_memory):
        """Creating a second related conversation should keep the first one."""
        from pyrit.backend.models.attacks import CreateConversationRequest
        from pyrit.models.conversation_reference import ConversationReference, ConversationType

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

    @pytest.mark.asyncio
    async def test_conversations_sorted_by_created_at_earliest_first(self, attack_service, mock_memory):
        """Conversations should be sorted by created_at with earliest first."""
        from pyrit.models.conversation_reference import ConversationReference, ConversationType

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

    @pytest.mark.asyncio
    async def test_empty_conversations_sorted_last(self, attack_service, mock_memory):
        """Conversations with no timestamp should appear at the bottom."""
        from pyrit.models.conversation_reference import ConversationReference, ConversationType

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

    @pytest.mark.asyncio
    async def test_empty_conversations_all_sort_last(self, attack_service, mock_memory):
        """Multiple empty conversations should all have created_at=None."""
        from pyrit.models.conversation_reference import ConversationReference, ConversationType

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

    @pytest.mark.asyncio
    async def test_create_related_conversation_uses_duplicate_branch(self, attack_service, mock_memory):
        """When source_conversation_id and cutoff_index are provided, duplication path is used."""
        from pyrit.backend.models.attacks import CreateConversationRequest

        ar = make_attack_result(conversation_id="attack-1")
        mock_memory.get_attack_results.return_value = [ar]

        with patch.object(attack_service, "_duplicate_conversation_up_to", return_value="branch-dup") as mock_dup:
            result = await attack_service.create_related_conversation_async(
                attack_result_id="attack-1",
                request=CreateConversationRequest(source_conversation_id="attack-1", cutoff_index=2),
            )

        assert result is not None
        assert result.conversation_id == "branch-dup"
        mock_dup.assert_called_once_with(source_conversation_id="attack-1", cutoff_index=2)

    @pytest.mark.asyncio
    async def test_add_message_merges_converter_identifiers_without_duplicates(self, attack_service, mock_memory):
        """Should merge new converter identifiers with existing attack identifiers by hash."""
        from pyrit.backend.models.attacks import AttackSummary, ConversationMessagesResponse

        existing_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.prompt_converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        duplicate_converter = ComponentIdentifier(
            class_name="ExistingConverter",
            class_module="pyrit.prompt_converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )
        new_converter = ComponentIdentifier(
            class_name="NewConverter",
            class_module="pyrit.prompt_converter",
            params={"supported_input_types": ("text",), "supported_output_types": ("text",)},
        )

        ar = make_attack_result(conversation_id="attack-1")
        # Rebuild the atomic_attack_identifier to include an existing converter child
        strategy = ar.get_attack_strategy_identifier()
        ar.atomic_attack_identifier = build_atomic_attack_identifier(
            attack_identifier=ComponentIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                children={
                    "objective_target": strategy.get_child("objective_target") if strategy else None,
                    "request_converters": [existing_converter],
                },
            ),
        )

        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="attack-1",
            send=False,
            converter_ids=["c-1", "c-2"],
        )

        with (
            patch("pyrit.backend.services.attack_service.get_converter_service") as mock_get_converter_service,
            patch.object(
                attack_service,
                "get_attack_async",
                new=AsyncMock(
                    return_value=AttackSummary(
                        attack_result_id="ar-attack-1",
                        conversation_id="attack-1",
                        attack_type="ManualAttack",
                        converters=[],
                        message_count=0,
                        labels={},
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )
                ),
            ),
            patch.object(
                attack_service,
                "get_conversation_messages_async",
                new=AsyncMock(return_value=ConversationMessagesResponse(conversation_id="attack-1", messages=[])),
            ),
        ):
            mock_converter_service = MagicMock()
            mock_converter_service.get_converter_objects_for_ids.return_value = [
                MagicMock(get_identifier=MagicMock(return_value=duplicate_converter)),
                MagicMock(get_identifier=MagicMock(return_value=new_converter)),
            ]
            mock_get_converter_service.return_value = mock_converter_service

            await attack_service.add_message_async(attack_result_id="attack-1", request=request)

        update_fields = mock_memory.update_attack_result_by_id.call_args[1]["update_fields"]
        persisted_identifiers = update_fields["attack_identifier"]["children"]["request_converters"]
        persisted_classes = [identifier["class_name"] for identifier in persisted_identifiers]

        assert persisted_classes.count("ExistingConverter") == 1
        assert persisted_classes.count("NewConverter") == 1

    def test_duplicate_conversation_up_to_adds_pieces_when_present(self, attack_service, mock_memory):
        """Should duplicate up to cutoff and persist duplicated pieces only when returned."""
        source_messages = [
            make_mock_piece(conversation_id="attack-1", sequence=0),
            make_mock_piece(conversation_id="attack-1", sequence=1),
            make_mock_piece(conversation_id="attack-1", sequence=2),
        ]
        mock_memory.get_conversation.return_value = source_messages
        duplicated_piece = make_mock_piece(conversation_id="branch-1", sequence=0)
        mock_memory.duplicate_messages.return_value = ("branch-1", [duplicated_piece])

        new_id = attack_service._duplicate_conversation_up_to(source_conversation_id="attack-1", cutoff_index=1)

        assert new_id == "branch-1"
        passed_messages = mock_memory.duplicate_messages.call_args[1]["messages"]
        assert [m.sequence for m in passed_messages] == [0, 1]
        mock_memory.add_message_pieces_to_memory.assert_called_once()

    def test_duplicate_conversation_up_to_skips_persist_when_no_duplicated_pieces(self, attack_service, mock_memory):
        """Should not write to memory when duplicate_messages returns no pieces."""
        mock_memory.get_conversation.return_value = [make_mock_piece(conversation_id="attack-1", sequence=0)]
        mock_memory.duplicate_messages.return_value = ("branch-empty", [])

        new_id = attack_service._duplicate_conversation_up_to(source_conversation_id="attack-1", cutoff_index=10)

        assert new_id == "branch-empty"
        mock_memory.add_message_pieces_to_memory.assert_not_called()


class TestAddMessageGuards:
    """Tests for target-mismatch and operator-mismatch guards in add_message_async."""

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_allows_matching_target(self, attack_service, mock_memory) -> None:
        """Should NOT raise when request target matches attack target."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]
        mock_memory.get_message_pieces.return_value = []
        mock_memory.get_conversation.return_value = []

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

    @pytest.mark.asyncio
    async def test_rejects_mismatched_operator(self, attack_service, mock_memory) -> None:
        """Should raise ValueError when request operator differs from existing operator."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

        existing_piece = make_mock_piece(conversation_id="test-id")
        existing_piece.labels = {"operator": "alice"}
        mock_memory.get_message_pieces.return_value = [existing_piece]

        request = AddMessageRequest(
            role="user",
            pieces=[MessagePieceRequest(original_value="Hello")],
            target_conversation_id="test-id",
            send=False,
            labels={"operator": "bob"},
        )

        with pytest.raises(ValueError, match="Operator mismatch"):
            await attack_service.add_message_async(attack_result_id="test-id", request=request)

    @pytest.mark.asyncio
    async def test_allows_matching_operator(self, attack_service, mock_memory) -> None:
        """Should NOT raise when request operator matches existing operator."""
        ar = make_attack_result(conversation_id="test-id")
        mock_memory.get_attack_results.return_value = [ar]

        existing_piece = make_mock_piece(conversation_id="test-id")
        existing_piece.labels = {"operator": "alice"}
        mock_memory.get_message_pieces.return_value = [existing_piece]
        mock_memory.get_conversation.return_value = []

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
