# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for ConversationManager and related helper functions.

The ConversationManager handles conversation state for attacks, including:
- Initializing attack context with prepended conversations
- Managing conversation history retrieval
- Setting system prompts for chat targets
- Processing prepended conversations for both chat and non-chat targets

Helper functions include:
- mark_messages_as_simulated: Converts assistant to simulated_assistant role
- get_adversarial_chat_messages: Transforms messages with swapped roles for adversarial chat
- build_conversation_context_string_async: Formats messages into context strings
- get_prepended_turn_count: Counts assistant messages in a conversation
"""

import uuid
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.executor.attack import ConversationManager, ConversationState
from pyrit.executor.attack.component import PrependedConversationConfig
from pyrit.executor.attack.component.conversation_manager import (
    build_conversation_context_string_async,
    get_adversarial_chat_messages,
    get_prepended_turn_count,
    mark_messages_as_simulated,
)
from pyrit.executor.attack.core import AttackContext
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.models import Message, MessagePiece, Score
from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptChatTarget, PromptTarget
from tests.unit.mocks import get_mock_scorer_identifier

# =============================================================================
# Test Context Class
# =============================================================================


class _TestAttackContext(AttackContext):
    """Concrete AttackContext for testing."""

    # Add last_score to match MultiTurnAttackContext behavior for testing
    last_score: Optional[Score] = None


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def attack_identifier() -> Dict[str, str]:
    """Create a sample attack identifier."""
    return {
        "__type__": "TestAttack",
        "__module__": "pyrit.executor.attack.test_attack",
        "id": str(uuid.uuid4()),
    }


@pytest.fixture
def mock_prompt_normalizer() -> MagicMock:
    """Create a mock prompt normalizer for testing."""
    normalizer = MagicMock(spec=PromptNormalizer)
    normalizer.convert_values = AsyncMock()
    return normalizer


@pytest.fixture
def mock_chat_target() -> MagicMock:
    """Create a mock chat target for testing."""
    target = MagicMock(spec=PromptChatTarget)
    target.set_system_prompt = MagicMock()
    target.get_identifier.return_value = {"id": "mock_chat_target_id"}
    return target


@pytest.fixture
def mock_prompt_target() -> MagicMock:
    """Create a mock prompt target (non-chat) for testing."""
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = {"id": "mock_target_id"}
    return target


@pytest.fixture
def sample_user_piece() -> MessagePiece:
    """Create a sample user message piece."""
    return MessagePiece(
        role="user",
        original_value="Hello, how are you?",
        original_value_data_type="text",
        conversation_id=str(uuid.uuid4()),
    )


@pytest.fixture
def sample_assistant_piece() -> MessagePiece:
    """Create a sample assistant message piece."""
    return MessagePiece(
        role="assistant",
        original_value="I'm doing well, thank you!",
        original_value_data_type="text",
        conversation_id=str(uuid.uuid4()),
    )


@pytest.fixture
def sample_system_piece() -> MessagePiece:
    """Create a sample system message piece."""
    return MessagePiece(
        role="system",
        original_value="You are a helpful assistant",
        original_value_data_type="text",
        conversation_id=str(uuid.uuid4()),
    )


@pytest.fixture
def sample_conversation(sample_user_piece: MessagePiece, sample_assistant_piece: MessagePiece) -> List[Message]:
    """Create a sample conversation with user and assistant messages."""
    return [
        Message(message_pieces=[sample_user_piece]),
        Message(message_pieces=[sample_assistant_piece]),
    ]


@pytest.fixture
def sample_score() -> Score:
    """Create a sample score for testing."""
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=["test"],
        score_value_description="Test score",
        score_rationale="Test rationale",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=get_mock_scorer_identifier(),
    )


@pytest.fixture
def mock_attack_context() -> _TestAttackContext:
    """Create a mock attack context."""
    params = AttackParameters(objective="Test objective")
    return _TestAttackContext(params=params)


# =============================================================================
# Test Class: Helper Functions
# =============================================================================


class TestMarkMessagesAsSimulated:
    """Tests for the mark_messages_as_simulated helper function."""

    def test_converts_assistant_to_simulated_assistant(self) -> None:
        """Test that assistant role is converted to simulated_assistant."""
        piece = MessagePiece(role="assistant", original_value="Hello", conversation_id="test")
        message = Message(message_pieces=[piece])

        result = mark_messages_as_simulated([message])

        assert len(result) == 1
        assert result[0].message_pieces[0].get_role_for_storage() == "simulated_assistant"
        assert result[0].message_pieces[0].api_role == "assistant"
        assert result[0].message_pieces[0].is_simulated is True

    def test_leaves_user_unchanged(self) -> None:
        """Test that user role is not changed."""
        piece = MessagePiece(role="user", original_value="Hello", conversation_id="test")
        message = Message(message_pieces=[piece])

        result = mark_messages_as_simulated([message])

        assert len(result) == 1
        assert result[0].message_pieces[0].get_role_for_storage() == "user"
        assert result[0].message_pieces[0].is_simulated is False

    def test_leaves_system_unchanged(self) -> None:
        """Test that system role is not changed."""
        piece = MessagePiece(role="system", original_value="You are helpful", conversation_id="test")
        message = Message(message_pieces=[piece])

        result = mark_messages_as_simulated([message])

        assert len(result) == 1
        assert result[0].message_pieces[0].get_role_for_storage() == "system"
        assert result[0].message_pieces[0].is_simulated is False

    def test_mixed_conversation(self) -> None:
        """Test marking a conversation with mixed roles."""
        user_piece = MessagePiece(role="user", original_value="Hello", conversation_id="test", sequence=1)
        assistant_piece = MessagePiece(role="assistant", original_value="Hi there", conversation_id="test", sequence=2)

        messages = [
            Message(message_pieces=[user_piece]),
            Message(message_pieces=[assistant_piece]),
        ]

        result = mark_messages_as_simulated(messages)

        assert len(result) == 2
        # User should be unchanged
        assert result[0].message_pieces[0].get_role_for_storage() == "user"
        assert result[0].is_simulated is False
        # Assistant should be converted
        assert result[1].message_pieces[0].get_role_for_storage() == "simulated_assistant"
        assert result[1].is_simulated is True
        assert result[1].api_role == "assistant"

    def test_empty_list(self) -> None:
        """Test with empty message list."""
        result = mark_messages_as_simulated([])
        assert result == []


class TestGetAdversarialChatMessages:
    """Tests for the get_adversarial_chat_messages helper function."""

    def test_swaps_user_to_assistant(self) -> None:
        """Test that user role becomes assistant in adversarial context."""
        piece = MessagePiece(role="user", original_value="User message", conversation_id="original")
        messages = [Message(message_pieces=[piece])]

        result = get_adversarial_chat_messages(
            messages,
            adversarial_chat_conversation_id="adversarial_conv",
            attack_identifier={"__type__": "TestAttack"},
            adversarial_chat_target_identifier={"id": "adversarial_target"},
        )

        assert len(result) == 1
        assert result[0].get_piece().api_role == "assistant"
        assert result[0].get_piece().conversation_id == "adversarial_conv"

    def test_swaps_assistant_to_user(self) -> None:
        """Test that assistant role becomes user in adversarial context."""
        piece = MessagePiece(role="assistant", original_value="Assistant message", conversation_id="original")
        messages = [Message(message_pieces=[piece])]

        result = get_adversarial_chat_messages(
            messages,
            adversarial_chat_conversation_id="adversarial_conv",
            attack_identifier={"__type__": "TestAttack"},
            adversarial_chat_target_identifier={"id": "adversarial_target"},
        )

        assert len(result) == 1
        assert result[0].get_piece().api_role == "user"

    def test_swaps_simulated_assistant_to_user(self) -> None:
        """Test that simulated_assistant role becomes user in adversarial context."""
        piece = MessagePiece(
            role="simulated_assistant",
            original_value="Simulated message",
            conversation_id="original",
        )
        messages = [Message(message_pieces=[piece])]

        result = get_adversarial_chat_messages(
            messages,
            adversarial_chat_conversation_id="adversarial_conv",
            attack_identifier={"__type__": "TestAttack"},
            adversarial_chat_target_identifier={"id": "adversarial_target"},
        )

        assert len(result) == 1
        assert result[0].get_piece().api_role == "user"

    def test_skips_system_messages(self) -> None:
        """Test that system messages are skipped."""
        system_piece = MessagePiece(role="system", original_value="System prompt", conversation_id="original")
        user_piece = MessagePiece(role="user", original_value="User message", conversation_id="original")
        messages = [
            Message(message_pieces=[system_piece]),
            Message(message_pieces=[user_piece]),
        ]

        result = get_adversarial_chat_messages(
            messages,
            adversarial_chat_conversation_id="adversarial_conv",
            attack_identifier={"__type__": "TestAttack"},
            adversarial_chat_target_identifier={"id": "adversarial_target"},
        )

        # Only user message should be present, system skipped
        assert len(result) == 1
        assert result[0].get_piece().original_value == "User message"

    def test_assigns_new_uuids(self) -> None:
        """Test that new UUIDs are assigned to transformed messages."""
        original_id = uuid.uuid4()
        piece = MessagePiece(id=original_id, role="user", original_value="Message", conversation_id="original")
        messages = [Message(message_pieces=[piece])]

        result = get_adversarial_chat_messages(
            messages,
            adversarial_chat_conversation_id="adversarial_conv",
            attack_identifier={"__type__": "TestAttack"},
            adversarial_chat_target_identifier={"id": "adversarial_target"},
        )

        # New ID should be different from original
        assert result[0].get_piece().id != original_id

    def test_preserves_message_content(self) -> None:
        """Test that message content is preserved during transformation."""
        piece = MessagePiece(
            role="user",
            original_value="Original content",
            converted_value="Converted content",
            original_value_data_type="text",
            converted_value_data_type="text",
            conversation_id="original",
        )
        messages = [Message(message_pieces=[piece])]

        result = get_adversarial_chat_messages(
            messages,
            adversarial_chat_conversation_id="adversarial_conv",
            attack_identifier={"__type__": "TestAttack"},
            adversarial_chat_target_identifier={"id": "adversarial_target"},
        )

        assert result[0].get_piece().original_value == "Original content"
        assert result[0].get_piece().converted_value == "Converted content"

    def test_empty_prepended_conversation(self) -> None:
        """Test with empty prepended conversation."""
        result = get_adversarial_chat_messages(
            [],
            adversarial_chat_conversation_id="adversarial_conv",
            attack_identifier={"__type__": "TestAttack"},
            adversarial_chat_target_identifier={"id": "adversarial_target"},
        )

        assert result == []

    def test_applies_labels(self) -> None:
        """Test that labels are applied to transformed messages."""
        piece = MessagePiece(role="user", original_value="Message", conversation_id="original")
        messages = [Message(message_pieces=[piece])]
        labels = {"category": "test", "source": "unit_test"}

        result = get_adversarial_chat_messages(
            messages,
            adversarial_chat_conversation_id="adversarial_conv",
            attack_identifier={"__type__": "TestAttack"},
            adversarial_chat_target_identifier={"id": "adversarial_target"},
            labels=labels,
        )

        assert result[0].get_piece().labels == labels


class TestBuildConversationContextStringAsync:
    """Tests for the build_conversation_context_string_async helper function."""

    @pytest.mark.asyncio
    async def test_formats_messages_into_context_string(self) -> None:
        """Test that messages are formatted into a context string."""
        user_piece = MessagePiece(role="user", original_value="Hello", conversation_id="test")
        assistant_piece = MessagePiece(role="assistant", original_value="Hi there", conversation_id="test")
        messages = [
            Message(message_pieces=[user_piece]),
            Message(message_pieces=[assistant_piece]),
        ]

        result = await build_conversation_context_string_async(messages)

        assert "Hello" in result
        assert "Hi there" in result

    @pytest.mark.asyncio
    async def test_returns_empty_string_for_empty_messages(self) -> None:
        """Test that empty list returns empty string."""
        result = await build_conversation_context_string_async([])

        assert result == ""


class TestGetPrependedTurnCount:
    """Tests for the get_prepended_turn_count helper function."""

    def test_counts_assistant_messages(self) -> None:
        """Test that assistant messages are counted as turns."""
        user_piece = MessagePiece(role="user", original_value="Hello", conversation_id="test")
        assistant_piece = MessagePiece(role="assistant", original_value="Hi", conversation_id="test")
        messages = [
            Message(message_pieces=[user_piece]),
            Message(message_pieces=[assistant_piece]),
            Message(message_pieces=[user_piece]),
            Message(message_pieces=[assistant_piece]),
        ]

        count = get_prepended_turn_count(messages)

        assert count == 2

    def test_returns_zero_for_none(self) -> None:
        """Test that None returns 0."""
        count = get_prepended_turn_count(None)
        assert count == 0

    def test_returns_zero_for_empty_list(self) -> None:
        """Test that empty list returns 0."""
        count = get_prepended_turn_count([])
        assert count == 0

    def test_ignores_user_and_system_messages(self) -> None:
        """Test that only assistant messages are counted."""
        user_piece = MessagePiece(role="user", original_value="Hello", conversation_id="test")
        system_piece = MessagePiece(role="system", original_value="System prompt", conversation_id="test")
        messages = [
            Message(message_pieces=[system_piece]),
            Message(message_pieces=[user_piece]),
            Message(message_pieces=[user_piece]),
        ]

        count = get_prepended_turn_count(messages)

        assert count == 0


# =============================================================================
# Test Class: ConversationState Dataclass
# =============================================================================


class TestConversationState:
    """Tests for the ConversationState dataclass."""

    def test_default_values(self) -> None:
        """Test ConversationState initialization with defaults."""
        state = ConversationState()

        assert state.turn_count == 0
        assert state.last_assistant_message_scores == []

    def test_with_custom_values(self, sample_score: Score) -> None:
        """Test ConversationState initialization with custom values."""
        state = ConversationState(turn_count=5, last_assistant_message_scores=[sample_score])

        assert state.turn_count == 5
        assert len(state.last_assistant_message_scores) == 1
        assert state.last_assistant_message_scores[0] == sample_score


# =============================================================================
# Test Class: ConversationManager Initialization
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestConversationManagerInitialization:
    """Tests for ConversationManager initialization."""

    def test_init_with_required_parameters(self, attack_identifier: Dict[str, str]) -> None:
        """Test initialization with only required parameters."""
        manager = ConversationManager(attack_identifier=attack_identifier)

        assert manager._attack_identifier == attack_identifier
        assert isinstance(manager._prompt_normalizer, PromptNormalizer)
        assert manager._memory is not None

    def test_init_with_custom_prompt_normalizer(
        self, attack_identifier: Dict[str, str], mock_prompt_normalizer: MagicMock
    ) -> None:
        """Test initialization with a custom prompt normalizer."""
        manager = ConversationManager(attack_identifier=attack_identifier, prompt_normalizer=mock_prompt_normalizer)

        assert manager._prompt_normalizer == mock_prompt_normalizer


# =============================================================================
# Test Class: Conversation Retrieval
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestConversationRetrieval:
    """Tests for conversation retrieval methods."""

    def test_get_conversation_returns_empty_list_when_no_messages(self, attack_identifier: Dict[str, str]) -> None:
        """Test get_conversation returns empty list for non-existent conversation."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        result = manager.get_conversation(conversation_id)

        assert result == []

    def test_get_conversation_returns_messages_in_order(
        self, attack_identifier: Dict[str, str], sample_conversation: List[Message]
    ) -> None:
        """Test get_conversation returns messages in order."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        # Add messages to the database
        for msg in sample_conversation:
            for piece in msg.message_pieces:
                piece.conversation_id = conversation_id
            manager._memory.add_message_to_memory(request=msg)

        result = manager.get_conversation(conversation_id)

        assert len(result) == 2
        assert result[0].message_pieces[0].api_role == "user"
        assert result[1].message_pieces[0].api_role == "assistant"

    def test_get_last_message_returns_none_for_empty_conversation(self, attack_identifier: Dict[str, str]) -> None:
        """Test get_last_message returns None for empty conversation."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        result = manager.get_last_message(conversation_id=conversation_id)

        assert result is None

    def test_get_last_message_returns_last_piece(
        self, attack_identifier: Dict[str, str], sample_conversation: List[Message]
    ) -> None:
        """Test get_last_message returns the most recent message."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        # Add messages to the database
        for msg in sample_conversation:
            for piece in msg.message_pieces:
                piece.conversation_id = conversation_id
            manager._memory.add_message_to_memory(request=msg)

        result = manager.get_last_message(conversation_id=conversation_id)

        assert result is not None
        assert result.api_role == "assistant"

    def test_get_last_message_with_role_filter(
        self, attack_identifier: Dict[str, str], sample_conversation: List[Message]
    ) -> None:
        """Test get_last_message with role filter returns correct message."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        # Add messages to the database
        for msg in sample_conversation:
            for piece in msg.message_pieces:
                piece.conversation_id = conversation_id
            manager._memory.add_message_to_memory(request=msg)

        # Get last user message
        result = manager.get_last_message(conversation_id=conversation_id, role="user")

        assert result is not None
        assert result.api_role == "user"

    def test_get_last_message_with_role_filter_returns_none_when_no_match(
        self, attack_identifier: Dict[str, str], sample_conversation: List[Message]
    ) -> None:
        """Test get_last_message returns None when no message matches role filter."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        # Add messages to the database
        for msg in sample_conversation:
            for piece in msg.message_pieces:
                piece.conversation_id = conversation_id
            manager._memory.add_message_to_memory(request=msg)

        # Try to get system message when none exists
        result = manager.get_last_message(conversation_id=conversation_id, role="system")

        assert result is None


# =============================================================================
# Test Class: System Prompt Handling
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestSystemPromptHandling:
    """Tests for system prompt functionality."""

    def test_set_system_prompt_with_chat_target(
        self, attack_identifier: Dict[str, str], mock_chat_target: MagicMock
    ) -> None:
        """Test set_system_prompt calls target's set_system_prompt method."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        system_prompt = "You are a helpful assistant"
        labels = {"type": "system"}

        manager.set_system_prompt(
            target=mock_chat_target,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            labels=labels,
        )

        mock_chat_target.set_system_prompt.assert_called_once_with(
            system_prompt=system_prompt,
            conversation_id=conversation_id,
            attack_identifier=attack_identifier,
            labels=labels,
        )

    def test_set_system_prompt_without_labels(
        self, attack_identifier: Dict[str, str], mock_chat_target: MagicMock
    ) -> None:
        """Test set_system_prompt works without labels."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        system_prompt = "You are a helpful assistant"

        manager.set_system_prompt(
            target=mock_chat_target,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
        )

        mock_chat_target.set_system_prompt.assert_called_once()
        call_args = mock_chat_target.set_system_prompt.call_args
        assert call_args.kwargs["labels"] is None


# =============================================================================
# Test Class: Initialize Context
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestInitializeContext:
    """Tests for initialize_context_async method."""

    @pytest.mark.asyncio
    async def test_raises_error_for_empty_conversation_id(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        mock_attack_context: AttackContext,
    ) -> None:
        """Test that empty conversation_id raises ValueError."""
        manager = ConversationManager(attack_identifier=attack_identifier)

        with pytest.raises(ValueError, match="conversation_id cannot be empty"):
            await manager.initialize_context_async(
                context=mock_attack_context,
                target=mock_chat_target,
                conversation_id="",
            )

    @pytest.mark.asyncio
    async def test_returns_default_state_for_no_prepended_conversation(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        mock_attack_context: AttackContext,
    ) -> None:
        """Test that no prepended conversation returns default state."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        state = await manager.initialize_context_async(
            context=mock_attack_context,
            target=mock_chat_target,
            conversation_id=conversation_id,
        )

        assert isinstance(state, ConversationState)
        assert state.turn_count == 0
        assert state.last_assistant_message_scores == []

    @pytest.mark.asyncio
    async def test_merges_memory_labels(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
    ) -> None:
        """Test that memory_labels are merged with context labels."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.memory_labels = {"context_key": "context_value"}

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
            memory_labels={"attack_key": "attack_value"},
        )

        # Both labels should be merged
        assert context.memory_labels["attack_key"] == "attack_value"
        assert context.memory_labels["context_key"] == "context_value"

    @pytest.mark.asyncio
    async def test_adds_prepended_conversation_to_memory_for_chat_target(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that prepended conversation is added to memory for chat targets."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
        )

        # Verify messages were added to memory
        stored = manager.get_conversation(conversation_id)
        assert len(stored) == 2

    @pytest.mark.asyncio
    async def test_converts_assistant_to_simulated_assistant(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_assistant_piece: MessagePiece,
    ) -> None:
        """Test that assistant messages are converted to simulated_assistant."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = [Message(message_pieces=[sample_assistant_piece])]

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
        )

        stored = manager.get_conversation(conversation_id)
        assert len(stored) == 1
        # Should be stored as simulated_assistant but api_role is still assistant
        assert stored[0].get_piece().get_role_for_storage() == "simulated_assistant"
        assert stored[0].get_piece().api_role == "assistant"

    @pytest.mark.asyncio
    async def test_normalizes_for_non_chat_target_by_default(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that prepended conversation is normalized for non-chat targets by default."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation
        context.next_message = None

        # By default, should normalize (not raise) - matching PrependedConversationConfig field default
        await manager.initialize_context_async(
            context=context,
            target=mock_prompt_target,
            conversation_id=conversation_id,
        )

        # next_message should now contain the normalized prepended context
        assert context.next_message is not None
        text_value = context.next_message.get_piece().original_value
        assert len(text_value) > 0

    @pytest.mark.asyncio
    async def test_normalizes_for_non_chat_target_when_configured(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that non-chat target normalizes prepended conversation when configured."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation
        context.next_message = Message.from_prompt(prompt="Next message", role="user")

        config = PrependedConversationConfig(non_chat_target_behavior="normalize_first_turn")

        await manager.initialize_context_async(
            context=context,
            target=mock_prompt_target,
            conversation_id=conversation_id,
            prepended_conversation_config=config,
        )

        # next_message should now contain the prepended context
        assert context.next_message is not None
        text_value = context.next_message.get_piece().original_value
        assert "Next message" in text_value
        assert "Hello" in text_value or "doing well" in text_value

    @pytest.mark.asyncio
    async def test_returns_turn_count_for_multi_turn_attacks(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that turn count is returned for multi-turn attacks."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation

        state = await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
            max_turns=10,
        )

        # sample_conversation has 1 assistant message = 1 turn
        assert state.turn_count == 1

    @pytest.mark.asyncio
    async def test_multipart_message_extracts_scores_from_all_pieces(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_score: Score,
    ) -> None:
        """Test that multi-part assistant messages extract scores from all pieces."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))

        # Create a multi-part assistant response (e.g., text + image)
        # All pieces in a Message must share the same conversation_id
        piece_conversation_id = str(uuid.uuid4())

        # Create score for first piece
        # Prepended conversations are simulated, so only false scores are extracted
        score1 = Score(
            score_type="true_false",
            score_value="false",
            score_category=["test"],
            score_value_description="Score for text piece",
            score_rationale="Test rationale for text",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=get_mock_scorer_identifier(),
        )
        piece1 = MessagePiece(
            role="assistant",
            original_value="Here is the analysis:",
            original_value_data_type="text",
            conversation_id=piece_conversation_id,
            scores=[score1],  # Attach score directly to piece
        )

        # Create score for second piece
        # Also false since prepended conversations only extract false scores
        score2 = Score(
            score_type="true_false",
            score_value="false",
            score_category=["test"],
            score_value_description="Score for image piece",
            score_rationale="Test rationale for image",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=get_mock_scorer_identifier(),
        )
        piece2 = MessagePiece(
            role="assistant",
            original_value="chart_image.png",
            original_value_data_type="image_path",
            conversation_id=piece_conversation_id,
            scores=[score2],  # Attach score directly to piece
        )

        multipart_response = Message(message_pieces=[piece1, piece2])
        context.prepended_conversation = [
            Message.from_prompt(prompt="Analyze data", role="user"),
            multipart_response,
        ]

        state = await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
            max_turns=10,
        )

        # Verify scores from both pieces are returned
        assert len(state.last_assistant_message_scores) == 2
        assert score1 in state.last_assistant_message_scores
        assert score2 in state.last_assistant_message_scores

    @pytest.mark.asyncio
    async def test_prepended_conversation_ignores_true_scores(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
    ) -> None:
        """Test that prepended conversations only extract false scores, ignoring true scores.

        Prepended conversations are simulated (not real target responses), so true scores
        would incorrectly indicate the objective was already achieved. Only false scores
        are extracted to provide feedback rationale for continued attack attempts.
        """
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))

        # Create a score with true value - should be ignored
        true_score = Score(
            score_type="true_false",
            score_value="true",
            score_category=["test"],
            score_value_description="Should be ignored",
            score_rationale="This simulated success should not be extracted",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=get_mock_scorer_identifier(),
        )

        # Create a score with false value - should be extracted
        false_score = Score(
            score_type="true_false",
            score_value="false",
            score_category=["test"],
            score_value_description="Should be extracted",
            score_rationale="This refusal can provide feedback",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=get_mock_scorer_identifier(),
        )

        piece_with_true = MessagePiece(
            role="assistant",
            original_value="Simulated success response",
            original_value_data_type="text",
            conversation_id=str(uuid.uuid4()),
            scores=[true_score],
        )

        piece_with_false = MessagePiece(
            role="assistant",
            original_value="Simulated refusal response",
            original_value_data_type="text",
            conversation_id=str(uuid.uuid4()),
            scores=[false_score],
        )

        # Test with true score only - should get no scores
        context.prepended_conversation = [
            Message.from_prompt(prompt="Test prompt", role="user"),
            Message(message_pieces=[piece_with_true]),
        ]

        state = await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
            max_turns=10,
        )

        assert len(state.last_assistant_message_scores) == 0
        assert context.last_score is None

        # Test with false score - should extract it
        context2 = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context2.prepended_conversation = [
            Message.from_prompt(prompt="Test prompt", role="user"),
            Message(message_pieces=[piece_with_false]),
        ]

        state2 = await manager.initialize_context_async(
            context=context2,
            target=mock_chat_target,
            conversation_id=str(uuid.uuid4()),
            max_turns=10,
        )

        assert len(state2.last_assistant_message_scores) == 1
        assert false_score in state2.last_assistant_message_scores
        assert context2.last_score == false_score


# =============================================================================
# Test Class: Prepended Conversation Config Settings
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestPrependedConversationConfigSettings:
    """Tests for PrependedConversationConfig settings in initialize_context_async."""

    # -------------------------------------------------------------------------
    # non_chat_target_behavior Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_non_chat_target_behavior_normalize_is_default(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that non-chat targets normalize by default (no config), matching dataclass field default."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation
        context.next_message = None

        # Should normalize by default (matching PrependedConversationConfig field default)
        await manager.initialize_context_async(
            context=context,
            target=mock_prompt_target,
            conversation_id=conversation_id,
        )

        # next_message should contain normalized context
        assert context.next_message is not None
        text_value = context.next_message.get_piece().original_value
        assert len(text_value) > 0

    @pytest.mark.asyncio
    async def test_non_chat_target_behavior_raise_explicit(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that non_chat_target_behavior='raise' raises ValueError."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation

        config = PrependedConversationConfig(non_chat_target_behavior="raise")

        with pytest.raises(
            ValueError, match="prepended_conversation requires the objective target to be a PromptChatTarget"
        ):
            await manager.initialize_context_async(
                context=context,
                target=mock_prompt_target,
                conversation_id=conversation_id,
                prepended_conversation_config=config,
            )

    @pytest.mark.asyncio
    async def test_non_chat_target_behavior_normalize_first_turn_creates_next_message(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that normalize_first_turn creates next_message when none exists."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation
        context.next_message = None

        config = PrependedConversationConfig(non_chat_target_behavior="normalize_first_turn")

        await manager.initialize_context_async(
            context=context,
            target=mock_prompt_target,
            conversation_id=conversation_id,
            prepended_conversation_config=config,
        )

        # Should have created a next_message with the normalized context
        assert context.next_message is not None
        text_value = context.next_message.get_piece().original_value
        assert len(text_value) > 0

    @pytest.mark.asyncio
    async def test_non_chat_target_behavior_normalize_first_turn_prepends_to_existing_message(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that normalize_first_turn prepends context to existing next_message."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation
        context.next_message = Message.from_prompt(prompt="My question", role="user")

        config = PrependedConversationConfig(non_chat_target_behavior="normalize_first_turn")

        await manager.initialize_context_async(
            context=context,
            target=mock_prompt_target,
            conversation_id=conversation_id,
            prepended_conversation_config=config,
        )

        # Should have prepended context to existing message
        text_value = context.next_message.get_piece().original_value
        assert "My question" in text_value
        # Context should come before the original question
        question_index = text_value.find("My question")
        assert question_index > 0  # Context should be prepended

    @pytest.mark.asyncio
    async def test_non_chat_target_behavior_normalize_returns_empty_state(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that normalize_first_turn returns empty ConversationState (no turn tracking)."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation

        config = PrependedConversationConfig(non_chat_target_behavior="normalize_first_turn")

        state = await manager.initialize_context_async(
            context=context,
            target=mock_prompt_target,
            conversation_id=conversation_id,
            prepended_conversation_config=config,
        )

        # Non-chat targets don't track turns
        assert state.turn_count == 0
        assert state.last_assistant_message_scores == []

    # -------------------------------------------------------------------------
    # apply_converters_to_roles Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_apply_converters_to_roles_default_applies_to_all(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that converters are applied to all roles by default."""
        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.convert_values = AsyncMock()
        manager = ConversationManager(attack_identifier=attack_identifier, prompt_normalizer=mock_normalizer)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation

        converter_config = [PromptConverterConfiguration(converters=[])]

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
            request_converters=converter_config,
        )

        # convert_values should be called for each message (both user and assistant)
        assert mock_normalizer.convert_values.call_count == 2

    @pytest.mark.asyncio
    async def test_apply_converters_to_roles_user_only(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that converters are applied only to user role when configured."""
        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.convert_values = AsyncMock()
        manager = ConversationManager(attack_identifier=attack_identifier, prompt_normalizer=mock_normalizer)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation

        config = PrependedConversationConfig(apply_converters_to_roles=["user"])
        converter_config = [PromptConverterConfiguration(converters=[])]

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
            request_converters=converter_config,
            prepended_conversation_config=config,
        )

        # convert_values should be called only for user message
        assert mock_normalizer.convert_values.call_count == 1

    @pytest.mark.asyncio
    async def test_apply_converters_to_roles_assistant_only(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that converters are applied only to assistant role when configured."""
        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.convert_values = AsyncMock()
        manager = ConversationManager(attack_identifier=attack_identifier, prompt_normalizer=mock_normalizer)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation

        config = PrependedConversationConfig(apply_converters_to_roles=["assistant"])
        converter_config = [PromptConverterConfiguration(converters=[])]

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
            request_converters=converter_config,
            prepended_conversation_config=config,
        )

        # convert_values should be called only for assistant message
        assert mock_normalizer.convert_values.call_count == 1

    @pytest.mark.asyncio
    async def test_apply_converters_to_roles_empty_list_skips_all(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that empty roles list means no converters applied to any role."""
        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.convert_values = AsyncMock()
        manager = ConversationManager(attack_identifier=attack_identifier, prompt_normalizer=mock_normalizer)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation

        config = PrependedConversationConfig(apply_converters_to_roles=[])
        converter_config = [PromptConverterConfiguration(converters=[])]

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
            request_converters=converter_config,
            prepended_conversation_config=config,
        )

        # convert_values should not be called since no roles are configured
        mock_normalizer.convert_values.assert_not_called()

    # -------------------------------------------------------------------------
    # message_normalizer Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_message_normalizer_default_uses_conversation_context_normalizer(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that default normalizer produces Turn N format."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation
        context.next_message = None

        config = PrependedConversationConfig(non_chat_target_behavior="normalize_first_turn")

        await manager.initialize_context_async(
            context=context,
            target=mock_prompt_target,
            conversation_id=conversation_id,
            prepended_conversation_config=config,
        )

        # Default ConversationContextNormalizer produces "Turn N:" format
        assert context.next_message is not None
        text_value = context.next_message.get_piece().original_value
        assert "Turn 1" in text_value or "turn 1" in text_value.lower()

    @pytest.mark.asyncio
    async def test_message_normalizer_custom_normalizer_is_used(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that custom message_normalizer is used when provided."""
        from pyrit.message_normalizer import MessageStringNormalizer

        # Create a mock normalizer that returns a specific format
        mock_normalizer = MagicMock(spec=MessageStringNormalizer)
        mock_normalizer.normalize_string_async = AsyncMock(return_value="CUSTOM_FORMAT: test content")

        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation
        context.next_message = None

        config = PrependedConversationConfig(
            non_chat_target_behavior="normalize_first_turn",
            message_normalizer=mock_normalizer,
        )

        await manager.initialize_context_async(
            context=context,
            target=mock_prompt_target,
            conversation_id=conversation_id,
            prepended_conversation_config=config,
        )

        # Verify custom normalizer was called
        mock_normalizer.normalize_string_async.assert_called_once()
        # Verify the custom format is in the message
        assert context.next_message is not None
        text_value = context.next_message.get_piece().original_value
        assert "CUSTOM_FORMAT: test content" in text_value

    # -------------------------------------------------------------------------
    # Factory Methods Tests
    # -------------------------------------------------------------------------

    def test_default_factory_creates_raise_behavior(self) -> None:
        """Test that PrependedConversationConfig.default() creates raise behavior."""
        config = PrependedConversationConfig.default()

        assert config.non_chat_target_behavior == "raise"
        assert config.message_normalizer is None
        # Should include all roles
        assert "user" in config.apply_converters_to_roles
        assert "assistant" in config.apply_converters_to_roles
        assert "system" in config.apply_converters_to_roles

    def test_for_non_chat_target_factory_creates_normalize_behavior(self) -> None:
        """Test that for_non_chat_target() creates normalize_first_turn behavior."""
        config = PrependedConversationConfig.for_non_chat_target()

        assert config.non_chat_target_behavior == "normalize_first_turn"

    def test_for_non_chat_target_with_custom_normalizer(self) -> None:
        """Test that for_non_chat_target() accepts custom message_normalizer."""
        from pyrit.message_normalizer import MessageStringNormalizer

        mock_normalizer = MagicMock(spec=MessageStringNormalizer)
        config = PrependedConversationConfig.for_non_chat_target(message_normalizer=mock_normalizer)

        assert config.message_normalizer == mock_normalizer
        assert config.non_chat_target_behavior == "normalize_first_turn"

    def test_for_non_chat_target_with_custom_roles(self) -> None:
        """Test that for_non_chat_target() accepts custom apply_converters_to_roles."""
        config = PrependedConversationConfig.for_non_chat_target(apply_converters_to_roles=["user"])

        assert config.apply_converters_to_roles == ["user"]
        assert config.non_chat_target_behavior == "normalize_first_turn"

    # -------------------------------------------------------------------------
    # Chat Target Behavior (Config has no effect)
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_chat_target_ignores_non_chat_target_behavior(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_conversation: List[Message],
    ) -> None:
        """Test that chat targets ignore non_chat_target_behavior setting."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = sample_conversation

        # Even with raise behavior, chat targets should work
        config = PrependedConversationConfig(non_chat_target_behavior="raise")

        state = await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
            prepended_conversation_config=config,
        )

        # Should succeed and add to memory
        stored = manager.get_conversation(conversation_id)
        assert len(stored) == 2
        assert state.turn_count == 1

    # -------------------------------------------------------------------------
    # Integration with max_turns validation
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_config_with_max_turns_validation(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
    ) -> None:
        """Test that config works correctly with max_turns validation."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))

        # Create conversation with 3 assistant messages (3 turns)
        messages = []
        for i in range(3):
            user_piece = MessagePiece(
                role="user",
                original_value=f"User message {i}",
                conversation_id="temp",
            )
            assistant_piece = MessagePiece(
                role="assistant",
                original_value=f"Assistant response {i}",
                conversation_id="temp",
            )
            messages.append(Message(message_pieces=[user_piece]))
            messages.append(Message(message_pieces=[assistant_piece]))

        context.prepended_conversation = messages
        config = PrependedConversationConfig(apply_converters_to_roles=["user"])

        # Should fail because 3 turns exceeds max_turns=2
        with pytest.raises(ValueError, match="exceeding max_turns"):
            await manager.initialize_context_async(
                context=context,
                target=mock_chat_target,
                conversation_id=conversation_id,
                prepended_conversation_config=config,
                max_turns=2,
            )


# =============================================================================
# Test Class: Add Prepended Conversation to Memory
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestAddPrependedConversationToMemory:
    """Tests for add_prepended_conversation_to_memory_async method."""

    @pytest.mark.asyncio
    async def test_adds_messages_to_memory(
        self,
        attack_identifier: Dict[str, str],
        sample_conversation: List[Message],
    ) -> None:
        """Test that messages are added to memory."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        turn_count = await manager.add_prepended_conversation_to_memory_async(
            prepended_conversation=sample_conversation,
            conversation_id=conversation_id,
        )

        stored = manager.get_conversation(conversation_id)
        assert len(stored) == 2
        assert turn_count == 1  # One assistant message

    @pytest.mark.asyncio
    async def test_assigns_conversation_id_to_all_pieces(
        self,
        attack_identifier: Dict[str, str],
        sample_conversation: List[Message],
    ) -> None:
        """Test that conversation_id is assigned to all message pieces."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        await manager.add_prepended_conversation_to_memory_async(
            prepended_conversation=sample_conversation,
            conversation_id=conversation_id,
        )

        stored = manager.get_conversation(conversation_id)
        for msg in stored:
            for piece in msg.message_pieces:
                assert piece.conversation_id == conversation_id

    @pytest.mark.asyncio
    async def test_assigns_attack_identifier_to_all_pieces(
        self,
        attack_identifier: Dict[str, str],
        sample_conversation: List[Message],
    ) -> None:
        """Test that attack_identifier is assigned to all message pieces."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        await manager.add_prepended_conversation_to_memory_async(
            prepended_conversation=sample_conversation,
            conversation_id=conversation_id,
        )

        stored = manager.get_conversation(conversation_id)
        for msg in stored:
            for piece in msg.message_pieces:
                assert piece.attack_identifier == attack_identifier

    @pytest.mark.asyncio
    async def test_raises_error_when_exceeds_max_turns(
        self,
        attack_identifier: Dict[str, str],
        sample_user_piece: MessagePiece,
        sample_assistant_piece: MessagePiece,
    ) -> None:
        """Test that exceeding max_turns raises ValueError."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        # Create conversation with 2 assistant messages
        conversation = [
            Message(message_pieces=[sample_user_piece]),
            Message(message_pieces=[sample_assistant_piece]),
            Message(message_pieces=[sample_user_piece]),
            Message(message_pieces=[sample_assistant_piece]),
        ]

        with pytest.raises(ValueError, match="exceeding max_turns"):
            await manager.add_prepended_conversation_to_memory_async(
                prepended_conversation=conversation,
                conversation_id=conversation_id,
                max_turns=1,
            )

    @pytest.mark.asyncio
    async def test_multipart_response_counts_as_one_turn(
        self,
        attack_identifier: Dict[str, str],
    ) -> None:
        """Test that a multi-part assistant response counts as only one turn."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        piece_conversation_id = str(uuid.uuid4())

        # Create a multi-part assistant response (e.g., text + image)
        multipart_pieces = [
            MessagePiece(
                role="assistant",
                original_value="Here is the image:",
                conversation_id=piece_conversation_id,
            ),
            MessagePiece(
                role="assistant",
                original_value="image_url_here",
                original_value_data_type="image_path",
                conversation_id=piece_conversation_id,
            ),
        ]
        multipart_response = Message(message_pieces=multipart_pieces)

        conversation = [
            Message.from_prompt(prompt="Generate an image", role="user"),
            multipart_response,
        ]

        turn_count = await manager.add_prepended_conversation_to_memory_async(
            prepended_conversation=conversation,
            conversation_id=conversation_id,
            max_turns=1,  # Should not raise - only 1 turn despite 2 pieces
        )

        assert turn_count == 1

    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_conversation(
        self,
        attack_identifier: Dict[str, str],
    ) -> None:
        """Test that empty conversation returns 0 turns."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        turn_count = await manager.add_prepended_conversation_to_memory_async(
            prepended_conversation=[],
            conversation_id=conversation_id,
        )

        assert turn_count == 0

    @pytest.mark.asyncio
    async def test_applies_converters_when_provided(
        self,
        attack_identifier: Dict[str, str],
        mock_prompt_normalizer: MagicMock,
        sample_user_piece: MessagePiece,
    ) -> None:
        """Test that converters are applied when provided."""
        manager = ConversationManager(attack_identifier=attack_identifier, prompt_normalizer=mock_prompt_normalizer)
        conversation_id = str(uuid.uuid4())
        conversation = [Message(message_pieces=[sample_user_piece])]
        converter_config = [PromptConverterConfiguration(converters=[])]

        await manager.add_prepended_conversation_to_memory_async(
            prepended_conversation=conversation,
            conversation_id=conversation_id,
            request_converters=converter_config,
        )

        # Verify convert_values was called
        mock_prompt_normalizer.convert_values.assert_called()

    @pytest.mark.asyncio
    async def test_handles_none_messages_gracefully(
        self,
        attack_identifier: Dict[str, str],
    ) -> None:
        """Test that None messages are handled gracefully."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        turn_count = await manager.add_prepended_conversation_to_memory_async(
            prepended_conversation=[None],  # type: ignore[list-item]
            conversation_id=conversation_id,
        )

        assert turn_count == 0


# =============================================================================
# Test Class: Edge Cases and Error Handling
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestEdgeCasesAndErrorHandling:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_preserves_piece_metadata(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_user_piece: MessagePiece,
    ) -> None:
        """Test that piece metadata is preserved during processing."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        # Add metadata to piece
        sample_user_piece.labels = {"test": "label"}
        sample_user_piece.prompt_metadata = {"key": "value", "count": 1}
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = [Message(message_pieces=[sample_user_piece])]

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
        )

        stored = manager.get_conversation(conversation_id)
        assert len(stored) == 1
        processed_piece = stored[0].message_pieces[0]
        assert processed_piece.labels == {"test": "label"}
        assert processed_piece.prompt_metadata == {"key": "value", "count": 1}

    @pytest.mark.asyncio
    async def test_preserves_original_and_converted_values(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_user_piece: MessagePiece,
    ) -> None:
        """Test that original and converted values are preserved."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())

        sample_user_piece.original_value = "Original message"
        sample_user_piece.converted_value = "Converted message"
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = [Message(message_pieces=[sample_user_piece])]

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
        )

        stored = manager.get_conversation(conversation_id)
        assert len(stored) == 1
        stored_piece = stored[0].get_piece()
        assert stored_piece.original_value == "Original message"
        assert stored_piece.converted_value == "Converted message"

    @pytest.mark.asyncio
    async def test_handles_system_messages_in_prepended_conversation(
        self,
        attack_identifier: Dict[str, str],
        mock_chat_target: MagicMock,
        sample_system_piece: MessagePiece,
        sample_user_piece: MessagePiece,
    ) -> None:
        """Test that system messages are handled in prepended conversation."""
        manager = ConversationManager(attack_identifier=attack_identifier)
        conversation_id = str(uuid.uuid4())
        context = _TestAttackContext(params=AttackParameters(objective="Test objective"))
        context.prepended_conversation = [
            Message(message_pieces=[sample_system_piece]),
            Message(message_pieces=[sample_user_piece]),
        ]

        await manager.initialize_context_async(
            context=context,
            target=mock_chat_target,
            conversation_id=conversation_id,
        )

        stored = manager.get_conversation(conversation_id)
        # Both system and user messages should be stored
        assert len(stored) == 2
        assert stored[0].get_piece().api_role == "system"
        assert stored[1].get_piece().api_role == "user"
