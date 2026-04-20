# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Consistency tests for attack parameter handling across all attack strategies.

These tests verify that all attacks handle objective, next_message, prepended_conversation,
and memory_labels consistently according to the established contracts.
"""

import uuid
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackScoringConfig,
    CrescendoAttack,
    PromptSendingAttack,
    RedTeamingAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig
from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import CentralMemory
from pyrit.models import (
    ChatMessageRole,
    Message,
    MessagePiece,
    PromptDataType,
    Score,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptChatTarget, PromptTarget
from pyrit.score import FloatScaleThresholdScorer, TrueFalseScorer


def _mock_scorer_id(name: str = "MockScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test_module",
        class_description="",
        identifier_type="instance",
    )


# =============================================================================
# Multi-Modal Message Fixtures
# =============================================================================


def _create_message_piece(
    *,
    role: ChatMessageRole = "user",
    value: str,
    data_type: PromptDataType = "text",
    conversation_id: str = "",
) -> MessagePiece:
    """Helper to create a message piece with consistent settings."""
    return MessagePiece(
        role=role,
        original_value=value,
        converted_value=value,
        original_value_data_type=data_type,
        converted_value_data_type=data_type,
        conversation_id=conversation_id,
    )


@pytest.fixture
def multimodal_text_message() -> Message:
    """Create a message with text content."""
    return Message.from_prompt(prompt="What is in this image?", role="user")


@pytest.fixture
def multimodal_image_message() -> Message:
    """Create a multi-modal message with text and image content."""
    conv_id = str(uuid.uuid4())
    return Message(
        message_pieces=[
            _create_message_piece(value="Describe the following image:", conversation_id=conv_id),
            _create_message_piece(value="base64encodedimagedata", data_type="image_path", conversation_id=conv_id),
        ]
    )


@pytest.fixture
def multimodal_audio_message() -> Message:
    """Create a multi-modal message with text and audio content."""
    conv_id = str(uuid.uuid4())
    return Message(
        message_pieces=[
            _create_message_piece(value="Transcribe this audio:", conversation_id=conv_id),
            _create_message_piece(value="base64encodedaudiodata", data_type="audio_path", conversation_id=conv_id),
        ]
    )


@pytest.fixture
def prepended_conversation_text() -> List[Message]:
    """Create a text-only prepended conversation."""
    return [
        Message.from_prompt(prompt="Hello, I need help with something.", role="user"),
        Message.from_prompt(prompt="Of course! How can I assist you today?", role="assistant"),
        Message.from_prompt(prompt="I'm working on a research project.", role="user"),
        Message.from_prompt(prompt="That sounds interesting. Tell me more about it.", role="assistant"),
    ]


@pytest.fixture
def prepended_conversation_multimodal() -> List[Message]:
    """Create a multimodal prepended conversation with image content."""
    conv_id = str(uuid.uuid4())
    return [
        Message(
            message_pieces=[
                _create_message_piece(value="Look at this diagram:", conversation_id=conv_id),
                _create_message_piece(value="base64diagram", data_type="image_path", conversation_id=conv_id),
            ]
        ),
        Message.from_prompt(prompt="I see a flowchart. What would you like to know?", role="assistant"),
    ]


# =============================================================================
# Mock Target Fixtures
# =============================================================================


@pytest.fixture
def mock_chat_target() -> MagicMock:
    """Create a mock PromptChatTarget with common setup."""
    target = MagicMock(spec=PromptChatTarget)
    target.send_prompt_async = AsyncMock()
    target.set_system_prompt = MagicMock()
    target.get_identifier.return_value = {"__type__": "MockChatTarget", "__module__": "test_module"}
    return target


@pytest.fixture
def mock_non_chat_target() -> MagicMock:
    """Create a mock PromptTarget (non-chat) with common setup."""
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.get_identifier.return_value = {"__type__": "MockTarget", "__module__": "test_module"}
    return target


@pytest.fixture
def mock_adversarial_chat() -> MagicMock:
    """Create a mock adversarial chat target."""
    target = MagicMock(spec=PromptChatTarget)
    target.send_prompt_async = AsyncMock()
    target.set_system_prompt = MagicMock()
    target.get_identifier.return_value = {"__type__": "MockAdversarialChat", "__module__": "test_module"}
    return target


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    """Create a mock true/false scorer."""
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock()
    scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")
    return scorer


@pytest.fixture
def mock_prompt_normalizer() -> MagicMock:
    """Create a mock prompt normalizer."""
    normalizer = MagicMock(spec=PromptNormalizer)
    normalizer.send_prompt_async = AsyncMock()
    return normalizer


@pytest.fixture
def sample_response() -> Message:
    """Create a sample response message."""
    return Message.from_prompt(prompt="This is a test response.", role="assistant")


@pytest.fixture
def success_score() -> Score:
    """Create a success score."""
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=["test"],
        score_value_description="Objective achieved",
        score_rationale="The objective was achieved.",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id("MockScorer"),
    )


@pytest.fixture
def failure_score() -> Score:
    """Create a failure score."""
    return Score(
        score_type="true_false",
        score_value="false",
        score_category=["test"],
        score_value_description="Objective not achieved",
        score_rationale="The objective was not achieved.",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id("MockScorer"),
    )


# =============================================================================
# Attack Fixtures
# =============================================================================


@pytest.fixture
def mock_refusal_scorer() -> MagicMock:
    """Create a mock refusal scorer that returns no refusal."""
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock(
        return_value=[
            Score(
                score_type="true_false",
                score_value="false",  # No refusal
                score_category=["refusal"],
                score_value_description="No refusal detected",
                score_rationale="Response was not a refusal",
                score_metadata={},
                message_piece_id=str(uuid.uuid4()),
                scorer_class_identifier=_mock_scorer_id("MockRefusalScorer"),
            )
        ]
    )
    scorer.get_identifier.return_value = _mock_scorer_id("MockRefusalScorer")
    return scorer


@pytest.fixture
def red_teaming_attack(
    mock_chat_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_objective_scorer: MagicMock,
    sample_response: Message,
    success_score: Score,
) -> RedTeamingAttack:
    """Create a pre-configured RedTeamingAttack with mocked normalizer."""
    mock_objective_scorer.score_async.return_value = [success_score]

    adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
    scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

    attack = RedTeamingAttack(
        objective_target=mock_chat_target,
        attack_adversarial_config=adversarial_config,
        attack_scoring_config=scoring_config,
        max_turns=10,
    )

    mock_normalizer = MagicMock(spec=PromptNormalizer)
    mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
    attack._prompt_normalizer = mock_normalizer

    return attack


@pytest.fixture
def crescendo_attack(
    mock_chat_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_objective_scorer: MagicMock,
    mock_refusal_scorer: MagicMock,
    sample_response: Message,
    success_score: Score,
) -> CrescendoAttack:
    """Create a pre-configured CrescendoAttack with mocked normalizer."""
    mock_objective_scorer.score_async.return_value = [success_score]

    adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
    scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer, refusal_scorer=mock_refusal_scorer)

    attack = CrescendoAttack(
        objective_target=mock_chat_target,
        attack_adversarial_config=adversarial_config,
        attack_scoring_config=scoring_config,
        max_turns=10,
    )

    mock_normalizer = MagicMock(spec=PromptNormalizer)
    mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
    attack._prompt_normalizer = mock_normalizer

    return attack


@pytest.fixture
def tap_attack(
    mock_chat_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_objective_scorer: MagicMock,
    sample_response: Message,
    success_score: Score,
) -> TreeOfAttacksWithPruningAttack:
    """Create a pre-configured TreeOfAttacksWithPruningAttack with mocked normalizer."""
    mock_objective_scorer.score_async.return_value = [success_score]

    # TAP requires a FloatScaleThresholdScorer for objective_scorer
    mock_threshold_scorer = MagicMock(spec=FloatScaleThresholdScorer)
    mock_threshold_scorer.threshold = 0.8
    mock_threshold_scorer.scorer_type = "true_false"
    mock_threshold_scorer.score_async = AsyncMock(return_value=[success_score])
    mock_threshold_scorer.get_identifier.return_value = _mock_scorer_id("FloatScaleThresholdScorer")

    adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
    scoring_config = TAPAttackScoringConfig(objective_scorer=mock_threshold_scorer)

    attack = TreeOfAttacksWithPruningAttack(
        objective_target=mock_chat_target,
        attack_adversarial_config=adversarial_config,
        attack_scoring_config=scoring_config,
        tree_width=1,
        tree_depth=5,
        branching_factor=1,
        on_topic_checking_enabled=False,
    )

    mock_normalizer = MagicMock(spec=PromptNormalizer)
    mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
    attack._prompt_normalizer = mock_normalizer

    return attack


# =============================================================================
# Test Class: next_message Handling
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestNextMessageSentFirst:
    """
    Tests verifying that next_message is used as the first message sent to the target.

    When next_message is provided in AttackParameters, attacks that accept it should:
    1. Send next_message content to the objective target (not the objective string)
    2. Preserve multi-modal content in the message
    """

    @pytest.mark.asyncio
    async def test_prompt_sending_attack_sends_next_message_multimodal(
        self, mock_chat_target: MagicMock, sample_response: Message, multimodal_image_message: Message
    ) -> None:
        """Test that PromptSendingAttack sends next_message with multimodal content preserved."""
        attack = PromptSendingAttack(objective_target=mock_chat_target)

        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
        attack._prompt_normalizer = mock_normalizer

        await attack.execute_async(
            objective="This objective should NOT be sent",
            next_message=multimodal_image_message,
        )

        call_args = mock_normalizer.send_prompt_async.call_args
        sent_message = call_args.kwargs.get("message")
        sent_target = call_args.kwargs.get("target")

        assert sent_target == mock_chat_target, "Message should be sent to the objective target"
        assert sent_message is not None, "No message was sent to the target"
        assert len(sent_message.message_pieces) == 2, "Multimodal message should have 2 pieces"
        assert sent_message.message_pieces[0].original_value_data_type == "text"
        assert sent_message.message_pieces[1].original_value_data_type == "image_path"
        assert "This objective should NOT be sent" not in sent_message.get_value()

    @pytest.mark.asyncio
    async def test_red_teaming_attack_uses_next_message_first_turn(
        self,
        mock_chat_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        sample_response: Message,
        success_score: Score,
        multimodal_image_message: Message,
    ) -> None:
        """Test that RedTeamingAttack uses next_message for the first turn, preserving multimodal content."""
        mock_objective_scorer.score_async.return_value = [success_score]

        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_chat_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=5,
        )

        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
        attack._prompt_normalizer = mock_normalizer

        await attack.execute_async(
            objective="Test objective",
            next_message=multimodal_image_message,
        )

        # The first message sent should contain the next_message content with image preserved
        first_call = mock_normalizer.send_prompt_async.call_args_list[0]
        sent_message = first_call.kwargs.get("message")
        sent_target = first_call.kwargs.get("target")

        assert sent_target == mock_chat_target, "First message should be sent to the objective target"
        assert sent_message is not None, "No message was sent to the target"
        assert len(sent_message.message_pieces) == 2, "Multimodal message should have 2 pieces (text + image)"
        assert sent_message.message_pieces[0].original_value_data_type == "text"
        assert sent_message.message_pieces[1].original_value_data_type == "image_path", (
            "Image content must be preserved"
        )

    @pytest.mark.asyncio
    async def test_crescendo_attack_uses_next_message_first_turn(
        self,
        mock_chat_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        sample_response: Message,
        success_score: Score,
        multimodal_image_message: Message,
    ) -> None:
        """Test that CrescendoAttack uses next_message for the first turn, preserving multimodal content."""
        mock_objective_scorer.score_async.return_value = [success_score]

        # Create refusal scorer mock
        mock_refusal_scorer = MagicMock(spec=TrueFalseScorer)
        mock_refusal_scorer.score_async = AsyncMock(
            return_value=[
                Score(
                    score_type="true_false",
                    score_value="false",  # No refusal
                    score_category=["refusal"],
                    score_value_description="No refusal detected",
                    score_rationale="Response was not a refusal",
                    score_metadata={},
                    message_piece_id=str(uuid.uuid4()),
                    scorer_class_identifier=_mock_scorer_id("MockRefusalScorer"),
                )
            ]
        )
        mock_refusal_scorer.get_identifier.return_value = _mock_scorer_id("MockRefusalScorer")

        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer, refusal_scorer=mock_refusal_scorer)

        attack = CrescendoAttack(
            objective_target=mock_chat_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=5,
        )

        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
        attack._prompt_normalizer = mock_normalizer

        await attack.execute_async(
            objective="Test objective",
            next_message=multimodal_image_message,
        )

        # The first message sent should contain the next_message content with image preserved
        first_call = mock_normalizer.send_prompt_async.call_args_list[0]
        sent_message = first_call.kwargs.get("message")
        sent_target = first_call.kwargs.get("target")

        assert sent_target == mock_chat_target, "First message should be sent to the objective target"
        assert sent_message is not None, "No message was sent to the target"
        assert len(sent_message.message_pieces) == 2, "Multimodal message should have 2 pieces (text + image)"
        assert sent_message.message_pieces[0].original_value_data_type == "text"
        assert sent_message.message_pieces[1].original_value_data_type == "image_path", (
            "Image content must be preserved"
        )

    @pytest.mark.asyncio
    async def test_tree_of_attacks_uses_next_message_first_turn(
        self,
        mock_chat_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        sample_response: Message,
        success_score: Score,
        multimodal_image_message: Message,
    ) -> None:
        """Test that TreeOfAttacksWithPruningAttack uses next_message for the first turn on all nodes."""
        mock_objective_scorer.score_async.return_value = [success_score]

        # TAP requires a FloatScaleThresholdScorer for objective_scorer
        mock_threshold_scorer = MagicMock(spec=FloatScaleThresholdScorer)
        mock_threshold_scorer.threshold = 0.8
        mock_threshold_scorer.scorer_type = "true_false"
        mock_threshold_scorer.score_async = AsyncMock(return_value=[success_score])
        mock_threshold_scorer.get_identifier.return_value = _mock_scorer_id("FloatScaleThresholdScorer")

        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = TAPAttackScoringConfig(objective_scorer=mock_threshold_scorer)

        attack = TreeOfAttacksWithPruningAttack(
            objective_target=mock_chat_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            tree_width=1,  # Single node for simpler testing
            tree_depth=1,
            branching_factor=1,
            on_topic_checking_enabled=False,  # Disable to simplify test
        )

        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
        attack._prompt_normalizer = mock_normalizer

        await attack.execute_async(
            objective="Test objective",
            next_message=multimodal_image_message,
        )

        # Find the call that sent the message to the objective target (not adversarial chat)
        # The objective target is mock_chat_target
        target_calls = [
            call
            for call in mock_normalizer.send_prompt_async.call_args_list
            if call.kwargs.get("target") == mock_chat_target
        ]

        assert len(target_calls) >= 1, "At least one message should be sent to objective target"
        first_target_call = target_calls[0]
        sent_message = first_target_call.kwargs.get("message")

        assert sent_message is not None, "No message was sent to the objective target"
        assert len(sent_message.message_pieces) == 2, "Multimodal message should have 2 pieces (text + image)"
        assert sent_message.message_pieces[0].original_value_data_type == "text"
        assert sent_message.message_pieces[1].original_value_data_type == "image_path", (
            "Image content must be preserved"
        )


# =============================================================================
# Test Class: prepended_conversation Memory Handling
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestPrependedConversationInMemory:
    """
    Tests verifying that prepended_conversation is properly added to memory.

    For PromptChatTargets, prepended_conversation should:
    1. Be added to memory with the correct conversation_id
    2. Have assistant messages translated to simulated_assistant role
    3. Preserve multi-modal content
    """

    def _assert_assistant_translated_to_simulated(
        self,
        *,
        conversation: List[Message],
        prepended_count: int,
    ) -> None:
        """
        Assert that assistant messages in prepended conversation are translated to simulated_assistant.

        Args:
            conversation: The full conversation from memory.
            prepended_count: Number of prepended messages to check (excludes actual responses).
        """
        prepended_in_memory = conversation[:prepended_count]

        # Verify at least one simulated assistant exists (use is_simulated property)
        simulated_assistant_pieces = [
            piece for msg in prepended_in_memory for piece in msg.message_pieces if piece.is_simulated
        ]
        assert len(simulated_assistant_pieces) >= 1, (
            "Assistant messages should be translated to simulated_assistant (is_simulated=True)"
        )

        # Verify no raw non-simulated "assistant" role remains in prepended messages
        raw_assistant_in_prepended = [
            piece
            for msg in prepended_in_memory
            for piece in msg.message_pieces
            if piece.api_role == "assistant" and not piece.is_simulated
        ]
        assert len(raw_assistant_in_prepended) == 0, "Prepended assistant messages should have is_simulated=True"

    @pytest.mark.asyncio
    async def test_prompt_sending_attack_adds_prepended_to_memory(
        self,
        mock_chat_target: MagicMock,
        sample_response: Message,
        prepended_conversation_multimodal: List[Message],
        sqlite_instance,
    ) -> None:
        """Test that prepended conversation is preserved in memory with correct role translation."""
        attack = PromptSendingAttack(objective_target=mock_chat_target)

        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
        attack._prompt_normalizer = mock_normalizer

        await attack.execute_async(
            objective="Test objective",
            prepended_conversation=prepended_conversation_multimodal,
        )

        call_args = mock_normalizer.send_prompt_async.call_args
        conversation_id = call_args.kwargs.get("conversation_id")

        memory = CentralMemory.get_memory_instance()
        conversation = list(memory.get_conversation(conversation_id=conversation_id))

        # Should have exactly the prepended messages in memory (mock normalizer doesn't add responses)
        assert len(conversation) == 2, f"Expected exactly 2 prepended messages, got {len(conversation)}"

        # Find the multimodal message in conversation - verify image content preserved
        image_pieces = [
            piece
            for msg in conversation
            for piece in msg.message_pieces
            if piece.original_value_data_type == "image_path"
        ]
        assert len(image_pieces) == 1, "Multimodal image content should be preserved in memory"

        # Verify assistant -> simulated_assistant translation
        self._assert_assistant_translated_to_simulated(
            conversation=conversation,
            prepended_count=len(prepended_conversation_multimodal),
        )

    @pytest.mark.asyncio
    async def test_red_teaming_attack_adds_prepended_to_memory(
        self,
        red_teaming_attack: RedTeamingAttack,
        prepended_conversation_multimodal: List[Message],
        sqlite_instance,
    ) -> None:
        """Test that RedTeamingAttack preserves prepended conversation in memory with role translation."""
        result = await red_teaming_attack.execute_async(
            objective="Test objective",
            prepended_conversation=prepended_conversation_multimodal,
        )

        memory = CentralMemory.get_memory_instance()
        conversation = list(memory.get_conversation(conversation_id=result.conversation_id))

        # Should have exactly the prepended messages in memory (mock normalizer doesn't add responses)
        assert len(conversation) == 2, f"Expected exactly 2 prepended messages, got {len(conversation)}"

        # Find the multimodal message in conversation - verify image content preserved
        image_pieces = [
            piece
            for msg in conversation
            for piece in msg.message_pieces
            if piece.original_value_data_type == "image_path"
        ]
        assert len(image_pieces) == 1, "Multimodal image content should be preserved in memory"

        # Verify assistant -> simulated_assistant translation
        self._assert_assistant_translated_to_simulated(
            conversation=conversation,
            prepended_count=len(prepended_conversation_multimodal),
        )

    @pytest.mark.asyncio
    async def test_crescendo_attack_adds_prepended_to_memory(
        self,
        crescendo_attack: CrescendoAttack,
        prepended_conversation_multimodal: List[Message],
        multimodal_text_message: Message,
        sqlite_instance,
    ) -> None:
        """Test that CrescendoAttack preserves prepended conversation in memory with role translation."""
        result = await crescendo_attack.execute_async(
            objective="Test objective",
            prepended_conversation=prepended_conversation_multimodal,
            next_message=multimodal_text_message,  # Required when prepended_conversation is provided
        )

        memory = CentralMemory.get_memory_instance()
        conversation = list(memory.get_conversation(conversation_id=result.conversation_id))

        # Should have exactly the prepended messages in memory (mock normalizer doesn't add responses)
        assert len(conversation) == 2, f"Expected exactly 2 prepended messages, got {len(conversation)}"

        # Find the multimodal message in conversation - verify image content preserved
        image_pieces = [
            piece
            for msg in conversation
            for piece in msg.message_pieces
            if piece.original_value_data_type == "image_path"
        ]
        assert len(image_pieces) == 1, "Multimodal image content should be preserved in memory"

        # Verify assistant -> simulated_assistant translation
        self._assert_assistant_translated_to_simulated(
            conversation=conversation,
            prepended_count=len(prepended_conversation_multimodal),
        )

    @pytest.mark.asyncio
    async def test_tap_attack_adds_prepended_to_memory(
        self,
        mock_chat_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
        sample_response: Message,
        success_score: Score,
        prepended_conversation_multimodal: List[Message],
        multimodal_text_message: Message,
        sqlite_instance,
    ) -> None:
        """Test that TreeOfAttacksWithPruningAttack preserves prepended conversation in memory."""
        mock_objective_scorer.score_async.return_value = [success_score]

        # TAP requires a FloatScaleThresholdScorer for objective_scorer
        mock_threshold_scorer = MagicMock(spec=FloatScaleThresholdScorer)
        mock_threshold_scorer.threshold = 0.8
        mock_threshold_scorer.scorer_type = "true_false"
        mock_threshold_scorer.score_async = AsyncMock(return_value=[success_score])
        mock_threshold_scorer.get_identifier.return_value = _mock_scorer_id("FloatScaleThresholdScorer")

        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = TAPAttackScoringConfig(objective_scorer=mock_threshold_scorer)

        attack = TreeOfAttacksWithPruningAttack(
            objective_target=mock_chat_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            tree_width=1,
            tree_depth=2,  # Need depth > 1 to allow for prepended turn
            branching_factor=1,
            on_topic_checking_enabled=False,
        )

        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
        attack._prompt_normalizer = mock_normalizer

        result = await attack.execute_async(
            objective="Test objective",
            prepended_conversation=prepended_conversation_multimodal,
            next_message=multimodal_text_message,  # Required when prepended_conversation is provided
        )

        memory = CentralMemory.get_memory_instance()
        conversation = list(memory.get_conversation(conversation_id=result.conversation_id))

        # Should have exactly the prepended messages in memory (mock normalizer doesn't add responses)
        assert len(conversation) == 2, f"Expected exactly 2 prepended messages, got {len(conversation)}"

        # Find the multimodal message in conversation - verify image content preserved
        image_pieces = [
            piece
            for msg in conversation
            for piece in msg.message_pieces
            if piece.original_value_data_type == "image_path"
        ]
        assert len(image_pieces) == 1, "Multimodal image content should be preserved in memory"

        # Verify assistant -> simulated_assistant translation
        self._assert_assistant_translated_to_simulated(
            conversation=conversation,
            prepended_count=len(prepended_conversation_multimodal),
        )


# =============================================================================
# Test Class: prepended_conversation executed_turns counting
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestMultiTurnTurnCounting:
    """
    Tests verifying that multi-turn attacks properly count prepended conversation turns.

    When prepended_conversation is provided:
    1. executed_turns should start at the count of assistant messages in prepended_conversation
    2. max_turns validation should account for prepended turns
    """

    @pytest.mark.asyncio
    async def test_red_teaming_starts_with_prepended_turn_count(
        self,
        red_teaming_attack: RedTeamingAttack,
        prepended_conversation_text: List[Message],
    ) -> None:
        """Test that RedTeamingAttack starts executed_turns at prepended turn count."""
        # The prepended_conversation_text has 2 assistant messages
        result = await red_teaming_attack.execute_async(
            objective="Test objective",
            prepended_conversation=prepended_conversation_text,
        )

        # The attack should have succeeded on first additional turn
        # Total turns = prepended (2) + executed (1) = 3
        assert result.executed_turns >= 2, "Turn count should include prepended turns"

    @pytest.mark.asyncio
    async def test_crescendo_starts_with_prepended_turn_count(
        self,
        crescendo_attack: CrescendoAttack,
        prepended_conversation_text: List[Message],
        multimodal_text_message: Message,
    ) -> None:
        """Test that CrescendoAttack starts executed_turns at prepended turn count."""
        # The prepended_conversation_text has 2 assistant messages
        result = await crescendo_attack.execute_async(
            objective="Test objective",
            prepended_conversation=prepended_conversation_text,
            next_message=multimodal_text_message,  # Required when prepended_conversation is provided
        )

        # Total turns = prepended (2) + executed (1) = 3
        assert result.executed_turns >= 2, "Turn count should include prepended turns"

    @pytest.mark.asyncio
    async def test_tap_starts_with_prepended_turn_count(
        self,
        tap_attack: TreeOfAttacksWithPruningAttack,
        prepended_conversation_text: List[Message],
        multimodal_text_message: Message,
    ) -> None:
        """Test that TreeOfAttacksWithPruningAttack starts executed_turns at prepended turn count."""
        # The prepended_conversation_text has 2 assistant messages
        result = await tap_attack.execute_async(
            objective="Test objective",
            prepended_conversation=prepended_conversation_text,
            next_message=multimodal_text_message,  # Required when prepended_conversation is provided
        )

        # Total turns should account for prepended turns
        assert result.executed_turns >= 2, "Turn count should include prepended turns"


# =============================================================================
# Test Class: memory_labels Propagation
# =============================================================================


@pytest.mark.usefixtures("patch_central_database")
class TestMemoryLabelsPropagation:
    """
    Tests verifying that memory_labels are properly propagated through attacks.

    memory_labels should be passed to all prompts sent via the target.
    """

    @pytest.mark.asyncio
    async def test_prompt_sending_attack_propagates_memory_labels(
        self, mock_chat_target: MagicMock, sample_response: Message, sqlite_instance
    ) -> None:
        """Test that PromptSendingAttack propagates memory_labels to prompts."""
        attack = PromptSendingAttack(objective_target=mock_chat_target)

        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)
        attack._prompt_normalizer = mock_normalizer

        test_labels = {"test_key": "test_value", "attack_type": "prompt_sending"}

        await attack.execute_async(
            objective="Test objective",
            memory_labels=test_labels,
        )

        call_args = mock_normalizer.send_prompt_async.call_args
        passed_labels = call_args.kwargs.get("labels")

        assert passed_labels is not None, "Labels should be passed to send_prompt_async"
        assert passed_labels["test_key"] == "test_value"


# =============================================================================
# Test Class: Adversarial Chat Context Injection
# =============================================================================


def _get_adversarial_chat_text_values(*, adversarial_chat_conversation_id: str) -> List[str]:
    """
    Get all text values from the adversarial chat conversation in memory.

    This includes both system prompts and conversation history messages.

    Args:
        adversarial_chat_conversation_id: The conversation ID for the adversarial chat.

    Returns:
        List of text values from all text pieces in the adversarial conversation.
    """
    memory = CentralMemory.get_memory_instance()
    conversation = list(memory.get_conversation(conversation_id=adversarial_chat_conversation_id))

    text_values = []
    for msg in conversation:
        for piece in msg.message_pieces:
            if piece.original_value_data_type == "text":
                text_values.append(piece.original_value)

    return text_values


def _assert_prepended_text_in_adversarial_context(
    *,
    prepended_conversation: List[Message],
    adversarial_chat_conversation_id: str,
    adversarial_chat_mock: Optional[MagicMock] = None,
) -> None:
    """
    Assert that text content from prepended conversation appears in adversarial chat context.

    Different attacks inject prepended conversation differently:
    - RedTeamingAttack: Adds messages to adversarial chat history
    - CrescendoAttack/TAP: Includes in adversarial chat system prompt

    This helper verifies the content appears regardless of the injection method by checking:
    1. Adversarial chat memory (history messages)
    2. The set_system_prompt call args (if mock provided and memory is empty)

    Args:
        prepended_conversation: The original prepended conversation.
        adversarial_chat_conversation_id: The adversarial chat's conversation ID.
        adversarial_chat_mock: Optional mock of adversarial chat target to check system prompt calls.

    Raises:
        AssertionError: If any prepended text content is not found in adversarial context.
    """
    adversarial_text_values = _get_adversarial_chat_text_values(
        adversarial_chat_conversation_id=adversarial_chat_conversation_id
    )

    # If memory is empty but we have a mock, check set_system_prompt calls
    if not adversarial_text_values and adversarial_chat_mock is not None:
        if adversarial_chat_mock.set_system_prompt.called:
            for call in adversarial_chat_mock.set_system_prompt.call_args_list:
                system_prompt = call.kwargs.get("system_prompt", "")
                if system_prompt:
                    adversarial_text_values.append(system_prompt)

    combined_adversarial_text = " ".join(adversarial_text_values)

    # Extract text values from prepended conversation
    for msg in prepended_conversation:
        for piece in msg.message_pieces:
            if piece.original_value_data_type == "text":
                assert piece.original_value in combined_adversarial_text, (
                    f"Prepended text '{piece.original_value}' not found in adversarial chat context. "
                    f"Available text: {adversarial_text_values}"
                )


@pytest.mark.usefixtures("patch_central_database")
class TestAdversarialChatContextInjection:
    """
    Tests verifying that prepended_conversation is properly injected into adversarial chat context.

    For multi-turn attacks with adversarial chat, prepended conversation should
    appear in adversarial chat's memory (either as history or in the system prompt).
    """

    @pytest.mark.asyncio
    async def test_red_teaming_injects_prepended_into_adversarial_context(
        self,
        red_teaming_attack: RedTeamingAttack,
        mock_adversarial_chat: MagicMock,
        prepended_conversation_text: List[Message],
        sqlite_instance,
    ) -> None:
        """Test that RedTeamingAttack injects prepended conversation into adversarial chat context."""
        result = await red_teaming_attack.execute_async(
            objective="Test objective",
            prepended_conversation=prepended_conversation_text,
        )

        # Get the adversarial chat conversation ID from related conversations
        adversarial_conv_refs = [
            ref for ref in result.related_conversations if ref.conversation_type.value == "adversarial"
        ]
        assert len(adversarial_conv_refs) >= 1, "Should have adversarial chat conversation reference"

        _assert_prepended_text_in_adversarial_context(
            prepended_conversation=prepended_conversation_text,
            adversarial_chat_conversation_id=adversarial_conv_refs[0].conversation_id,
            adversarial_chat_mock=mock_adversarial_chat,
        )

    @pytest.mark.asyncio
    async def test_crescendo_injects_prepended_into_adversarial_context(
        self,
        crescendo_attack: CrescendoAttack,
        mock_adversarial_chat: MagicMock,
        prepended_conversation_text: List[Message],
        multimodal_text_message: Message,
        sqlite_instance,
    ) -> None:
        """Test that CrescendoAttack injects prepended conversation into adversarial chat context."""
        result = await crescendo_attack.execute_async(
            objective="Test objective",
            prepended_conversation=prepended_conversation_text,
            next_message=multimodal_text_message,
        )

        # Get the adversarial chat conversation ID from related conversations
        adversarial_conv_refs = [
            ref for ref in result.related_conversations if ref.conversation_type.value == "adversarial"
        ]
        assert len(adversarial_conv_refs) >= 1, "Should have adversarial chat conversation reference"

        _assert_prepended_text_in_adversarial_context(
            prepended_conversation=prepended_conversation_text,
            adversarial_chat_conversation_id=adversarial_conv_refs[0].conversation_id,
            adversarial_chat_mock=mock_adversarial_chat,
        )

    @pytest.mark.asyncio
    async def test_tap_injects_prepended_into_adversarial_context(
        self,
        tap_attack: TreeOfAttacksWithPruningAttack,
        mock_adversarial_chat: MagicMock,
        prepended_conversation_text: List[Message],
        multimodal_text_message: Message,
        sqlite_instance,
    ) -> None:
        """Test that TreeOfAttacksWithPruningAttack injects prepended conversation into adversarial context."""
        # TAP may fail due to JSON parsing, but set_system_prompt should be called before the error
        try:
            await tap_attack.execute_async(
                objective="Test objective",
                prepended_conversation=prepended_conversation_text,
                next_message=multimodal_text_message,
            )
        except Exception:
            pass  # Expected - JSON parsing may fail, but set_system_prompt should have been called

        # Verify prepended text appears in adversarial context (checks mock's set_system_prompt calls)
        _assert_prepended_text_in_adversarial_context(
            prepended_conversation=prepended_conversation_text,
            adversarial_chat_conversation_id="",  # Empty - will fall back to mock check
            adversarial_chat_mock=mock_adversarial_chat,
        )
