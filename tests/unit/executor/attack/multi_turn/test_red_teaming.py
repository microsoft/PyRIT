# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackParameters,
    AttackScoringConfig,
    ConversationSession,
    ConversationState,
    MultiTurnAttackContext,
    RedTeamingAttack,
    RTASystemPromptPaths,
)
from pyrit.executor.attack.core.attack_config import DEFAULT_ADVERSARIAL_FIRST_MESSAGE
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    Message,
    MessagePiece,
    Score,
    SeedPrompt,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import Scorer, TrueFalseScorer


def _adversarial_reply_message(next_message: str = "Adversarial next message") -> Message:
    """Build an adversarial-chat reply Message in the canonical ``adversarial_chat`` JSON shape.

    Every genuine adversarial-conversation attack now validates the adversarial reply against the
    shared ``adversarial_chat`` schema, so a mocked reply must be JSON carrying ``next_message`` (plus
    the ``rationale`` / ``last_response_summary`` the schema requires) rather than raw text.
    """
    payload = json.dumps(
        {
            "next_message": next_message,
            "rationale": "test rationale",
            "last_response_summary": "test summary",
        }
    )
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=payload,
                original_value_data_type="text",
                converted_value=payload,
                converted_value_data_type="text",
            )
        ]
    )


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


@pytest.fixture
def mock_objective_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.get_identifier.return_value = _mock_target_id("MockTarget")
    # Sensible default: text-only target. Tests that need multimodal targets
    # override input_modalities / output_modalities on their own copy.
    target.configuration.capabilities.input_modalities = frozenset({frozenset({"text"})})
    target.configuration.capabilities.output_modalities = frozenset({frozenset({"text"})})
    return target


@pytest.fixture
def mock_adversarial_chat() -> MagicMock:
    chat = MagicMock(spec=PromptTarget)
    chat.send_prompt_async = AsyncMock()
    chat.set_system_prompt = MagicMock()
    chat.get_identifier.return_value = _mock_target_id("MockChatTarget")
    chat.configuration.capabilities.input_modalities = frozenset({frozenset({"text"})})
    chat.configuration.capabilities.output_modalities = frozenset({frozenset({"text"})})
    return chat


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock()
    scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")
    return scorer


@pytest.fixture
def mock_prompt_normalizer() -> MagicMock:
    normalizer = MagicMock(spec=PromptNormalizer)
    normalizer.send_prompt_async = AsyncMock()
    return normalizer


@pytest.fixture
def basic_context() -> MultiTurnAttackContext:
    return MultiTurnAttackContext(
        params=AttackParameters(objective="Test objective"),
        session=ConversationSession(),
    )


@pytest.fixture
def sample_response() -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="Test response",
                original_value_data_type="text",
                converted_value="Test response",
                converted_value_data_type="text",
            )
        ]
    )


@pytest.fixture
def success_score() -> Score:
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=["test"],
        score_value_description="Test success score",
        score_rationale="Test rationale for success",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id("MockScorer"),
    )


@pytest.fixture
def failure_score() -> Score:
    return Score(
        score_type="true_false",
        score_value="false",
        score_category=["test"],
        score_value_description="Test failure score",
        score_rationale="Test rationale for failure",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id("MockScorer"),
    )


@pytest.fixture
def float_score() -> Score:
    return Score(
        score_type="float_scale",
        score_value="0.9",
        score_category=["test"],
        score_value_description="High score",
        score_rationale="Test rationale for high score",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id("MockScorer"),
    )


@pytest.mark.usefixtures("patch_central_database")
class TestRedTeamingAttackInitialization:
    """Tests for RedTeamingAttack initialization and configuration"""

    def test_init_with_minimal_required_parameters(
        self, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock, mock_adversarial_chat: MagicMock
    ):
        """Test that attack initializes correctly with only required parameters."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        assert attack._objective_target == mock_objective_target
        assert attack._objective_scorer == mock_objective_scorer
        assert attack._adversarial_chat == mock_adversarial_chat
        assert isinstance(attack._prompt_normalizer, PromptNormalizer)
        assert attack._max_turns == 10  # Default value

    @pytest.mark.parametrize(
        "system_prompt_path",
        [
            RTASystemPromptPaths.TEXT_GENERATION.value,
            RTASystemPromptPaths.IMAGE_GENERATION.value,
            RTASystemPromptPaths.NAIVE_CRESCENDO.value,
            RTASystemPromptPaths.VIOLENT_DURIAN.value,
        ],
    )
    def test_init_with_different_system_prompts(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        system_prompt_path: Path,
    ):
        """Test that attack initializes correctly with different system prompt paths."""
        adversarial_config = AttackAdversarialConfig(
            target=mock_adversarial_chat, system_prompt=SeedPrompt.from_yaml_file(system_prompt_path)
        )
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        assert attack._adversarial_chat_system_prompt_template is not None
        assert attack._adversarial_chat_system_prompt_template.parameters is not None
        assert "objective" in attack._adversarial_chat_system_prompt_template.parameters

    @pytest.mark.parametrize(
        "seed_prompt,expected_value,expected_type",
        [
            ("Custom seed prompt", "Custom seed prompt", str),
            (SeedPrompt(value="Custom seed", data_type="text"), "Custom seed", SeedPrompt),
        ],
    )
    def test_init_with_seed_prompt_variations(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        seed_prompt: str | SeedPrompt,
        expected_value: str,
        expected_type: type,
    ):
        """Test that attack handles different seed prompt input types correctly."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat, first_message=seed_prompt)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        assert attack._adversarial_chat_first_message.value == expected_value
        if expected_type is str:
            assert attack._adversarial_chat_first_message.data_type == "text"

    def test_init_with_invalid_system_prompt_path_raises_error(self, mock_adversarial_chat: MagicMock):
        """Test that loading a nonexistent system prompt path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            AttackAdversarialConfig(
                target=mock_adversarial_chat, system_prompt=SeedPrompt.from_yaml_file("nonexistent_file.yaml")
            )

    def test_init_with_all_custom_configurations(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
    ):
        """Test that attack initializes correctly with all custom configurations."""
        converter_config = AttackConverterConfig()
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer, use_score_as_feedback=True)
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_converter_config=converter_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
            max_turns=20,  # Custom max turns
        )

        assert attack._request_converters == converter_config.request_converters
        assert attack._response_converters == converter_config.response_converters
        assert attack._prompt_normalizer == mock_prompt_normalizer
        assert attack._max_turns == 20

    def test_init_without_objective_scorer_raises_error(
        self, mock_objective_target: MagicMock, mock_adversarial_chat: MagicMock
    ):
        """Test that initialization without objective scorer raises ValueError."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig()  # No objective_scorer

        with pytest.raises(ValueError, match="Objective scorer must be provided"):
            RedTeamingAttack(
                objective_target=mock_objective_target,
                attack_adversarial_config=adversarial_config,
                attack_scoring_config=scoring_config,
            )

    @pytest.mark.parametrize(
        "missing_capability",
        ["multi_turn", "system_prompt"],
    )
    def test_init_rejects_adversarial_chat_missing_native_capability(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        missing_capability: str,
    ):
        """Adversarial chat must natively support MULTI_TURN and SYSTEM_PROMPT."""
        from pyrit.prompt_target.common.target_capabilities import CapabilityName

        mock_adversarial_chat.configuration.includes.side_effect = lambda *, capability: (
            capability != CapabilityName(missing_capability)
        )
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match=f"RedTeamingAttack .*{missing_capability}"):
            RedTeamingAttack(
                objective_target=mock_objective_target,
                attack_adversarial_config=adversarial_config,
                attack_scoring_config=scoring_config,
            )

    def test_get_objective_target_returns_correct_target(
        self, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock, mock_adversarial_chat: MagicMock
    ):
        """Test that get_objective_target returns the target passed during initialization."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        assert attack.get_objective_target() == mock_objective_target

    def test_get_attack_scoring_config_returns_config(
        self, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock, mock_adversarial_chat: MagicMock
    ):
        """Test that get_attack_scoring_config returns the configured AttackScoringConfig."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(
            objective_scorer=mock_objective_scorer,
            use_score_as_feedback=True,
        )

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        result = attack.get_attack_scoring_config()

        assert result.objective_scorer == mock_objective_scorer
        assert result.use_score_as_feedback is True


@pytest.mark.usefixtures("patch_central_database")
class TestContextCreation:
    """Tests for context creation from parameters"""

    async def test_execute_async_creates_context_properly(
        self, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock, mock_adversarial_chat: MagicMock
    ):
        """Test that execute_async creates context properly."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=15,
        )

        # Mock the execution methods
        with patch.object(attack, "_validate_context") as mock_validate:
            with patch.object(attack, "_setup_async", new_callable=AsyncMock):
                with patch.object(attack, "_perform_async", new_callable=AsyncMock) as mock_perform:
                    with patch.object(attack, "_teardown_async", new_callable=AsyncMock):
                        captured_context = None

                        async def capture_context(*args, **kwargs):
                            # Capture the context that was created
                            nonlocal captured_context
                            captured_context = kwargs.get("context")
                            return AttackResult(
                                conversation_id="test-id",
                                objective="Test objective",
                                outcome=AttackOutcome.SUCCESS,
                                executed_turns=1,
                            )

                        mock_perform.side_effect = capture_context

                        # Execute
                        await attack.execute_async(
                            objective="Test objective",
                            memory_labels={"test": "label"},
                        )

                        # Verify the captured context
                        assert captured_context is not None
                        assert captured_context.objective == "Test objective"
                        assert (
                            captured_context.prepended_conversation is None
                            or captured_context.prepended_conversation == []
                        )
                        assert captured_context.memory_labels == {"test": "label"}

                        # Verify that validation was called
                        mock_validate.assert_called_once()

    async def test_execute_async_with_custom_prompt(
        self, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock, mock_adversarial_chat: MagicMock
    ):
        """Test context creation with custom prompt."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        with patch.object(attack, "_validate_context"):
            with patch.object(attack, "_setup_async", new_callable=AsyncMock):
                with patch.object(attack, "_perform_async", new_callable=AsyncMock) as mock_perform:
                    with patch.object(attack, "_teardown_async", new_callable=AsyncMock):
                        captured_context = None

                        async def capture_context(*args, **kwargs):
                            nonlocal captured_context
                            captured_context = kwargs.get("context")
                            return AttackResult(
                                conversation_id="test-id",
                                objective="Test objective",
                                outcome=AttackOutcome.SUCCESS,
                                executed_turns=1,
                            )

                        mock_perform.side_effect = capture_context

                        # Execute with custom message
                        custom_message = Message.from_prompt(prompt="My custom prompt", role="user")
                        await attack.execute_async(
                            objective="Test objective",
                            next_message=custom_message,
                        )

                        # Verify the captured context
                        assert captured_context is not None
                        assert captured_context.next_message is not None
                        assert captured_context.next_message.message_pieces[0].original_value == "My custom prompt"

    async def test_execute_async_invalid_message_type(
        self, mock_objective_target: MagicMock, mock_objective_scorer: MagicMock, mock_adversarial_chat: MagicMock
    ):
        """Test that non-Message message parameter causes an error during execution."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        # Should raise RuntimeError when trying to use invalid message type
        with pytest.raises(RuntimeError):
            await attack.execute_async(
                objective="Test objective",
                next_message=123,  # Invalid type
            )


@pytest.mark.usefixtures("patch_central_database")
class TestContextValidation:
    """Tests for context validation logic"""

    @pytest.mark.parametrize(
        "objective,max_turns,executed_turns,expected_error",
        [
            ("", 5, 0, "Attack objective must be provided"),
            ("Test objective", 5, 5, "Already exceeded max turns"),
            ("Test objective", 5, 6, "Already exceeded max turns"),
        ],
    )
    def test_validate_context_raises_errors(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        objective: str,
        max_turns: int,
        executed_turns: int,
        expected_error: str,
    ):
        """Test that context validation raises appropriate errors for invalid inputs."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=max_turns,
        )
        context = MultiTurnAttackContext(
            params=AttackParameters(objective=objective),
            executed_turns=executed_turns,
        )

        with pytest.raises(ValueError, match=expected_error):
            attack._validate_context(context=context)

    def test_validate_context_with_valid_context(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that valid context passes validation without errors."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=10,
        )
        attack._validate_context(context=basic_context)  # Should not raise

    def test_init_with_invalid_max_turns(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that initialization with invalid max_turns raises ValueError."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match="Maximum turns must be a positive integer"):
            RedTeamingAttack(
                objective_target=mock_objective_target,
                attack_adversarial_config=adversarial_config,
                attack_scoring_config=scoring_config,
                max_turns=0,
            )

    async def test_max_turns_validation_with_prepended_conversation(
        self,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that prepended conversation turns are validated against max_turns."""
        # Create a separate chat target for objective since prepended_conversation requires PromptTarget
        mock_chat_objective_target = MagicMock(spec=PromptTarget)
        mock_chat_objective_target.send_prompt_async = AsyncMock()
        mock_chat_objective_target.set_system_prompt = MagicMock()
        mock_chat_objective_target.get_identifier.return_value = _mock_target_id("MockChatTarget")
        mock_chat_objective_target.configuration.capabilities.input_modalities = frozenset({frozenset({"text"})})
        mock_chat_objective_target.configuration.capabilities.output_modalities = frozenset({frozenset({"text"})})

        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_chat_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=1,  # Less than prepended turns (2)
        )

        # Create prepended conversation with 2 assistant messages
        prepended = [
            Message.from_prompt(prompt="Hello", role="user"),
            Message.from_prompt(prompt="Hi there!", role="assistant"),
            Message.from_prompt(prompt="How are you?", role="user"),
            Message.from_prompt(prompt="I'm fine!", role="assistant"),
        ]

        # Should raise RuntimeError wrapping ValueError because prepended turns (2) exceed max_turns (1)
        with pytest.raises(RuntimeError, match="exceeding max_turns"):
            await attack.execute_async(
                objective="Test objective",
                prepended_conversation=prepended,
            )


@pytest.mark.usefixtures("patch_central_database")
class TestSetupPhase:
    """Tests for the setup phase of the attack."""

    async def test_setup_initializes_conversation_session(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that setup correctly initializes a conversation session."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        # Mock conversation manager
        mock_state = ConversationState(turn_count=0)
        with patch.object(attack._conversation_manager, "initialize_context_async", return_value=mock_state):
            await attack._setup_async(context=basic_context)

        assert basic_context.session is not None
        assert isinstance(basic_context.session, ConversationSession)

    async def test_setup_updates_turn_count_from_prepended_conversation(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that setup updates turn count based on prepended conversation state."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        # Mock that simulates initialize_context_async setting executed_turns
        async def mock_initialize(*, context, **kwargs):
            context.executed_turns = 3
            return ConversationState(turn_count=3)

        with patch.object(attack._conversation_manager, "initialize_context_async", side_effect=mock_initialize):
            await attack._setup_async(context=basic_context)

        assert basic_context.executed_turns == 3

    async def test_setup_merges_memory_labels_correctly(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that memory labels from attack and context are merged correctly."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        # Add memory labels to both attack and context
        attack._memory_labels = {"strategy_label": "strategy_value", "common": "strategy"}
        basic_context.memory_labels = {"context_label": "context_value", "common": "context"}
        basic_context.prepended_conversation = [
            Message.from_prompt(prompt="prepended user", role="user"),
            Message.from_prompt(prompt="prepended assistant", role="assistant"),
        ]
        attack._memory = MagicMock()

        # Mock that simulates initialize_context_async merging labels
        async def mock_initialize(*, context, memory_labels=None, **kwargs):
            from pyrit.common.utils import combine_dict

            context.memory_labels = combine_dict(existing_dict=memory_labels, new_dict=context.memory_labels)
            return ConversationState(turn_count=0)

        with patch.object(attack._conversation_manager, "initialize_context_async", side_effect=mock_initialize):
            await attack._setup_async(context=basic_context)

        # Context labels should override strategy labels for common keys
        assert basic_context.memory_labels == {
            "strategy_label": "strategy_value",
            "context_label": "context_value",
            "common": "context",
        }
        added_message = attack._memory.add_message_to_memory.call_args_list[0].kwargs["request"]
        assert added_message.message_pieces[0].labels == basic_context.memory_labels

    async def test_setup_sets_adversarial_chat_system_prompt(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that setup correctly sets the adversarial chat system prompt."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        # Mock conversation manager
        mock_state = ConversationState(turn_count=0)
        with patch.object(attack._conversation_manager, "initialize_context_async", return_value=mock_state):
            await attack._setup_async(context=basic_context)

        # Verify system prompt was set
        mock_adversarial_chat.set_system_prompt.assert_called_once()
        call_args = mock_adversarial_chat.set_system_prompt.call_args
        assert "Test objective" in call_args.kwargs["system_prompt"]
        assert call_args.kwargs["conversation_id"] == basic_context.session.adversarial_chat_conversation_id

    async def test_setup_retrieves_last_score_matching_scorer_type(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
        success_score: Score,
    ):
        """Test that setup correctly retrieves the last score matching the objective scorer type."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        # Mock conversation state with scores
        other_score = Score(
            score_type="float_scale",
            score_value="0.5",
            score_category=["other"],
            score_value_description="Other score",
            score_rationale="Other rationale",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_mock_scorer_id("OtherScorer"),
        )

        mock_state = ConversationState(
            turn_count=1,
            last_assistant_message_scores=[other_score, success_score],
        )

        # The mock needs to also set context.last_score as a side effect (simulating what the real method does)
        async def mock_initialize(context, **kwargs):
            # Simulate the side effect of initialize_context_async setting the first true_false score
            for score in mock_state.last_assistant_message_scores:
                if score.score_type == "true_false":
                    context.last_score = score
                    break
            return mock_state

        with patch.object(
            attack._conversation_manager, "initialize_context_async", side_effect=mock_initialize
        ) as mock_init:
            await attack._setup_async(context=basic_context)

        assert basic_context.last_score == success_score


@pytest.mark.usefixtures("patch_central_database")
class TestPromptGeneration:
    """Tests for prompt generation logic"""

    async def test_generate_next_prompt_uses_custom_prompt_on_first_turn(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that custom prompt is used when provided and cleared after use."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        first_prompt = "Custom first prompt"
        basic_context.executed_turns = 0
        basic_context.next_message = Message.from_prompt(prompt=first_prompt, role="user")

        result = await attack._generate_next_prompt_async(context=basic_context)

        assert result.get_value() == first_prompt
        assert basic_context.next_message is None  # Should be cleared after use
        # Should not call adversarial chat
        mock_prompt_normalizer.send_prompt_async.assert_not_called()

    async def test_generate_next_prompt_uses_custom_prompt_regardless_of_turn(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that custom prompt is used even when executed_turns > 0."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        custom_prompt = "Custom prompt at turn 1"
        basic_context.executed_turns = 1  # Not first turn
        basic_context.next_message = Message.from_prompt(prompt=custom_prompt, role="user")

        result = await attack._generate_next_prompt_async(context=basic_context)

        assert result.get_value() == custom_prompt
        assert basic_context.next_message is None  # Should be cleared after use
        # Should not call adversarial chat
        mock_prompt_normalizer.send_prompt_async.assert_not_called()

    async def test_generate_next_prompt_uses_adversarial_chat_after_first_turn(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
    ):
        """Test that adversarial chat is used to generate prompts when no custom prompt."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        basic_context.executed_turns = 1
        basic_context.next_message = None  # No message
        basic_context.memory_labels = {"test": "label"}
        # The manager always validates the adversarial reply against the canonical adversarial_chat
        # schema, so the reply is JSON and ``next_message`` is extracted as the objective prompt.
        adversarial_response = _adversarial_reply_message("Adversarial next message")
        mock_prompt_normalizer.send_prompt_async.return_value = adversarial_response

        result = await attack._generate_next_prompt_async(context=basic_context)

        assert result.get_value() == "Adversarial next message"
        mock_prompt_normalizer.send_prompt_async.assert_called_once()
        sent_message = mock_prompt_normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent_message.message_pieces[0].labels == basic_context.memory_labels

    async def test_send_prompt_to_objective_target_applies_memory_labels(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
    ):
        """Test that memory labels are applied before sending prompts to the objective target."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)
        mock_objective_target.configuration.includes.return_value = True

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        basic_context.memory_labels = {"test": "label"}
        message = Message.from_prompt(prompt="target prompt", role="user")
        mock_prompt_normalizer.send_prompt_async.return_value = sample_response

        result = await attack._send_prompt_to_objective_target_async(context=basic_context, message=message)

        assert result == sample_response
        sent_message = mock_prompt_normalizer.send_prompt_async.call_args.kwargs["message"]
        assert sent_message.message_pieces[0].labels == basic_context.memory_labels

    async def test_generate_next_prompt_raises_on_none_response(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that ValueError is raised when adversarial chat returns None."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        basic_context.executed_turns = 1
        mock_prompt_normalizer.send_prompt_async.return_value = None

        with pytest.raises(ValueError, match="No response received for conversation ID"):
            await attack._generate_next_prompt_async(context=basic_context)


@pytest.mark.usefixtures("patch_central_database")
class TestResponseScoring:
    """Tests for response scoring logic."""

    async def test_score_response_successful(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
        success_score: Score,
    ):
        """Test successful scoring of a response."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        basic_context.last_response = sample_response
        # basic_context fixture already has objective="Test objective"

        # Configure the mock scorer to return the success_score
        mock_objective_scorer.score_async = AsyncMock(return_value=[success_score])

        result = await attack._score_response_async(context=basic_context)

        assert result == success_score

    async def test_score_response_returns_none_for_blocked(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that blocked responses return None without scoring."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        response_piece = MagicMock(spec=MessagePiece)
        response_piece.is_blocked.return_value = True

        basic_context.last_response = MagicMock(spec=Message)
        basic_context.last_response.get_piece.return_value = response_piece

        # Configure the mock scorer to return empty list for blocked response
        mock_objective_scorer.score_async = AsyncMock(return_value=[])

        result = await attack._score_response_async(context=basic_context)

        assert result is None

    async def test_score_response_returns_none_when_no_response(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that None is returned when no response is available."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        basic_context.last_response = None

        result = await attack._score_response_async(context=basic_context)

        assert result is None


@pytest.mark.usefixtures("patch_central_database")
class TestAttackExecution:
    """Tests for the main attack execution logic."""

    async def test_perform_attack_with_message_bypasses_adversarial_chat_on_first_turn(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
        success_score: Score,
    ):
        """Test that providing a message parameter bypasses adversarial chat generation on first turn."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        inline_scorer = MagicMock(spec=TrueFalseScorer)
        inline_scorer.get_identifier.return_value = _mock_scorer_id()
        scoring_config = AttackScoringConfig(objective_scorer=inline_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        # Set message to bypass adversarial chat
        custom_message = Message.from_prompt(prompt="Custom first turn message", role="user")
        basic_context.next_message = custom_message

        # Mock only objective target response (no adversarial chat should be called)
        mock_prompt_normalizer.send_prompt_async.return_value = sample_response

        with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=success_score):
            result = await attack._perform_async(context=basic_context)

        assert isinstance(result, AttackResult)
        assert result.outcome == AttackOutcome.SUCCESS
        assert result.executed_turns == 1

        # Verify adversarial chat was not called for first turn
        # (only objective target should receive the custom message)
        assert mock_prompt_normalizer.send_prompt_async.call_count == 1

        # Verify the message was cleared after use
        assert basic_context.next_message is None

    async def test_perform_async_sets_atomic_attack_identifier(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
        success_score: Score,
    ):
        """Test that _perform_async sets atomic_attack_identifier in the correct AtomicAttack format."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        inline_scorer = MagicMock(spec=TrueFalseScorer)
        inline_scorer.get_identifier.return_value = _mock_scorer_id()
        scoring_config = AttackScoringConfig(objective_scorer=inline_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        basic_context.next_message = Message.from_prompt(prompt="Test message", role="user")
        mock_prompt_normalizer.send_prompt_async.return_value = sample_response

        with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=success_score):
            result = await attack._perform_async(context=basic_context)

        assert result.atomic_attack_identifier is not None
        assert result.atomic_attack_identifier.class_name == "AtomicAttack"
        assert result.get_attack_strategy_identifier() == attack.get_identifier()

    async def test_perform_attack_with_multi_piece_message_uses_first_piece(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
        success_score: Score,
    ):
        """Test that multi-piece messages use only the first piece's converted_value."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        inline_scorer = MagicMock(spec=TrueFalseScorer)
        inline_scorer.get_identifier.return_value = _mock_scorer_id()
        scoring_config = AttackScoringConfig(objective_scorer=inline_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        # Create multi-piece message (e.g., text + image scenario)
        piece1 = MessagePiece(
            role="user",
            original_value="First piece text",
            converted_value="First piece text",
            conversation_id="test-conv",
            sequence=1,
        )
        piece2 = MessagePiece(
            role="user",
            original_value="Second piece text",
            converted_value="Second piece text",
            conversation_id="test-conv",
            sequence=1,
        )
        multi_piece_message = Message(message_pieces=[piece1, piece2])
        basic_context.next_message = multi_piece_message

        mock_prompt_normalizer.send_prompt_async.return_value = sample_response

        with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=success_score):
            result = await attack._perform_async(context=basic_context)

        # Verify the attack used the first piece's value
        assert result.outcome == AttackOutcome.SUCCESS

        # The send call should have been made with the first piece's text
        # (the attack extracts just the prompt text, not the whole message)
        assert mock_prompt_normalizer.send_prompt_async.call_count == 1

    @pytest.mark.parametrize(
        "score_value,expected_achieved",
        [
            ("true", True),
            ("false", False),
        ],
    )
    async def test_perform_attack_with_different_score_outcomes(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
        score_value: str,
        expected_achieved: bool,
    ):
        """Test attack execution with different score outcomes.

        The threshold logic is now handled by the scorer (e.g., FloatScaleThresholdScorer),
        which returns true_false scores. The attack checks the score value directly.
        """

        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        # Create true_false score (threshold comparison is done by the scorer)
        score = Score(
            score_type="true_false",
            score_value=score_value,
            score_category=["test"],
            score_value_description=f"Score: {score_value}",
            score_rationale="Test rationale",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_mock_scorer_id("MockScorer"),
        )

        # Mock methods
        with patch.object(attack, "_generate_next_prompt_async", new_callable=AsyncMock, return_value="Attack prompt"):
            with patch.object(
                attack,
                "_send_prompt_to_objective_target_async",
                new_callable=AsyncMock,
                return_value=sample_response,
            ):
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=score):
                    result = await attack._perform_async(context=basic_context)

        assert (result.outcome == AttackOutcome.SUCCESS) == expected_achieved

    async def test_perform_attack_reaches_max_turns(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that attack stops after reaching max turns."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
            max_turns=3,  # Set max turns in attack init
        )

        # Mock methods to always fail
        with (
            patch.object(
                attack, "_generate_next_prompt_async", new_callable=AsyncMock, return_value="Attack prompt"
            ) as mock_generate,
            patch.object(
                attack,
                "_send_prompt_to_objective_target_async",
                new_callable=AsyncMock,
                return_value=sample_response,
            ) as mock_send,
            patch.object(
                attack, "_score_response_async", new_callable=AsyncMock, return_value=failure_score
            ) as mock_score,
        ):
            result = await attack._perform_async(context=basic_context)

        assert result.outcome == AttackOutcome.FAILURE
        assert result.executed_turns == 3
        assert mock_generate.call_count == 3
        assert mock_send.call_count == 3
        assert mock_score.call_count == 3

    async def test_perform_builds_adversarial_manager_once_and_threads_it(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
        failure_score: Score,
    ):
        """The adversarial manager is built once per run and the same instance is reused every turn.

        Regression guard for the build-once refactor: the manager's conversation scope (ids,
        objective, memory labels) is fixed for the run, so rebuilding it each turn would waste work
        and risk drift. This pins that _perform_async builds it exactly once and threads that same
        instance into every _generate_next_prompt_async call.
        """
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=mock_prompt_normalizer,
            max_turns=3,
        )

        sentinel_manager = MagicMock()

        with (
            patch.object(attack, "_build_adversarial_manager", return_value=sentinel_manager) as mock_build,
            patch.object(
                attack, "_generate_next_prompt_async", new_callable=AsyncMock, return_value="Attack prompt"
            ) as mock_generate,
            patch.object(
                attack,
                "_send_prompt_to_objective_target_async",
                new_callable=AsyncMock,
                return_value=sample_response,
            ),
            patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=failure_score),
        ):
            await attack._perform_async(context=basic_context)

        # Built exactly once for the whole run, not once per turn.
        mock_build.assert_called_once()
        # Every turn received that same manager instance.
        assert mock_generate.call_count == 3
        assert all(call.kwargs["adversarial_manager"] is sentinel_manager for call in mock_generate.call_args_list)


@pytest.mark.usefixtures("patch_central_database")
class TestAttackLifecycle:
    """Tests for the complete attack lifecycle (execute_async)"""

    async def test_execute_async_successful_lifecycle(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        sample_response: Message,
        success_score: Score,
    ):
        """Test successful execution of complete attack lifecycle using execute_async."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=5,
        )

        # Mock all lifecycle methods
        with patch.object(attack, "_validate_context"):
            with patch.object(attack, "_setup_async", new_callable=AsyncMock):
                with patch.object(attack, "_perform_async", new_callable=AsyncMock) as mock_perform:
                    with patch.object(attack, "_teardown_async", new_callable=AsyncMock):
                        # Configure the return value for _perform_async
                        mock_perform.return_value = AttackResult(
                            conversation_id="test-conversation-id",
                            objective="Test objective",
                            outcome=AttackOutcome.SUCCESS,
                            executed_turns=1,
                            last_response=sample_response.get_piece(),
                            last_score=success_score,
                        )

                        # Execute using execute_async
                        result = await attack.execute_async(
                            objective="Test objective",
                        )

        # Verify result and proper execution order
        assert isinstance(result, AttackResult)
        assert result.outcome == AttackOutcome.SUCCESS

    async def test_execute_async_validation_failure_prevents_execution(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that validation failure prevents attack execution when using execute_async."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        # Mock validation to fail
        with patch.object(attack, "_validate_context", side_effect=ValueError("Invalid context")) as mock_validate:
            with patch.object(attack, "_setup_async", new_callable=AsyncMock) as mock_setup:
                with patch.object(attack, "_perform_async", new_callable=AsyncMock) as mock_perform:
                    with patch.object(attack, "_teardown_async", new_callable=AsyncMock) as mock_teardown:
                        # Should raise ValueError
                        with pytest.raises(ValueError) as exc_info:
                            await attack.execute_async(
                                objective="Test objective",
                            )

                        # Verify error details
                        assert "Strategy context validation failed for RedTeamingAttack" in str(
                            exc_info.value
                        )  # Verify only validation was attempted
        mock_validate.assert_called_once()
        mock_setup.assert_not_called()
        mock_perform.assert_not_called()
        mock_teardown.assert_not_called()

    async def test_execute_with_context_async_successful(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
        success_score: Score,
    ):
        """Test successful execution using execute_with_context_async."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        # Mock all lifecycle methods
        with patch.object(attack, "_validate_context"):
            with patch.object(attack, "_setup_async", new_callable=AsyncMock):
                with patch.object(attack, "_perform_async", new_callable=AsyncMock) as mock_perform:
                    with patch.object(attack, "_teardown_async", new_callable=AsyncMock):
                        # Configure the return value for _perform_async
                        mock_perform.return_value = AttackResult(
                            conversation_id=basic_context.session.conversation_id,
                            objective=basic_context.objective,
                            outcome=AttackOutcome.SUCCESS,
                            executed_turns=1,
                            last_response=sample_response.get_piece(),
                            last_score=success_score,
                        )

                        # Execute using execute_with_context_async
                        result = await attack.execute_with_context_async(context=basic_context)

        # Verify result
        assert isinstance(result, AttackResult)
        assert result.outcome == AttackOutcome.SUCCESS
        assert result.objective == basic_context.objective

    async def test_teardown_async_is_noop(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that teardown completes without errors."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        # Should complete without error
        await attack._teardown_async(context=basic_context)
        # No assertions needed - we just want to ensure it runs without exceptions


@pytest.mark.usefixtures("patch_central_database")
class TestRedTeamingConversationTracking:
    """Test that adversarial chat conversation IDs are properly tracked."""

    async def test_setup_tracks_adversarial_chat_conversation_id(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that setup adds the adversarial chat conversation ID to the context's tracking."""
        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        # Mock the conversation manager to return a state
        with patch.object(attack._conversation_manager, "initialize_context_async") as mock_update:
            mock_update.return_value = ConversationState(turn_count=0, last_assistant_message_scores=[])

            # Run setup
            await attack._setup_async(context=basic_context)

            # Verify the adversarial chat conversation ID is tracked
            assert (
                ConversationReference(
                    conversation_id=basic_context.session.adversarial_chat_conversation_id,
                    conversation_type=ConversationType.ADVERSARIAL,
                )
                in basic_context.related_conversations
            )

    async def test_attack_result_includes_adversarial_chat_conversation_ids(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
        success_score: Score,
    ):
        """Test that the attack result includes the tracked adversarial chat conversation IDs."""
        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        # Create a Message for the generated prompt
        generated_message = Message.from_prompt(prompt="Test prompt", role="user")

        with (
            patch.object(attack._conversation_manager, "initialize_context_async") as mock_update,
            patch.object(attack._prompt_normalizer, "send_prompt_async", new_callable=AsyncMock) as mock_send,
            patch.object(Scorer, "score_response_async", new_callable=AsyncMock) as mock_score,
            patch.object(attack, "_generate_next_prompt_async", new_callable=AsyncMock) as mock_generate,
        ):
            mock_update.return_value = ConversationState(turn_count=0, last_assistant_message_scores=[])
            mock_send.return_value = sample_response
            mock_score.return_value = {"objective_scores": [success_score]}
            mock_generate.return_value = generated_message
            mock_objective_scorer.score_async = AsyncMock(return_value=[success_score])

            # Run setup and attack
            await attack._setup_async(context=basic_context)
            result = await attack._perform_async(context=basic_context)

            # Verify the result includes the adversarial chat conversation IDs
            assert (
                ConversationReference(
                    conversation_id=basic_context.session.adversarial_chat_conversation_id,
                    conversation_type=ConversationType.ADVERSARIAL,
                )
                in result.related_conversations
            )

    async def test_adversarial_chat_conversation_id_uniqueness(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Test that adversarial chat conversation IDs are unique when added to the set."""
        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        # Mock the conversation manager
        with patch.object(attack._conversation_manager, "initialize_context_async") as mock_update:
            mock_update.return_value = ConversationState(turn_count=0, last_assistant_message_scores=[])

            # Run setup
            await attack._setup_async(context=basic_context)

            # Get the conversation ID
            conversation_id = basic_context.session.adversarial_chat_conversation_id

            # Verify it was added
            assert (
                ConversationReference(
                    conversation_id=conversation_id,
                    conversation_type=ConversationType.ADVERSARIAL,
                )
                in basic_context.related_conversations
            )
            assert len(basic_context.related_conversations) == 1

            # Try to add the same ID again (should not affect the set)
            basic_context.related_conversations.add(
                ConversationReference(
                    conversation_id=conversation_id,
                    conversation_type=ConversationType.ADVERSARIAL,
                )
            )

            # Verify it's still only one entry
            assert len(basic_context.related_conversations) == 1


@pytest.mark.usefixtures("patch_central_database")
class TestScoreLastTurnOnly:
    """Tests for the score_last_turn_only functionality."""

    def test_init_score_last_turn_only_defaults_to_false(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that score_last_turn_only defaults to False."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )

        assert attack._score_last_turn_only is False

    def test_init_score_last_turn_only_can_be_set_to_true(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that score_last_turn_only can be set to True."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            score_last_turn_only=True,
        )

        assert attack._score_last_turn_only is True

    async def test_score_last_turn_only_skips_intermediate_scoring(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that intermediate turns are not scored when score_last_turn_only=True."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=3,
            score_last_turn_only=True,
        )

        # Mock methods
        with patch.object(attack, "_generate_next_prompt_async", new_callable=AsyncMock) as mock_gen:
            with patch.object(attack, "_send_prompt_to_objective_target_async", new_callable=AsyncMock) as mock_send:
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock) as mock_score:
                    mock_gen.return_value = "test prompt"
                    mock_send.return_value = sample_response
                    mock_score.return_value = failure_score

                    context = MultiTurnAttackContext(
                        params=AttackParameters(objective="Test objective"), session=ConversationSession()
                    )

                    await attack._perform_async(context=context)

                    # Should only score the last turn (turn 3)
                    assert mock_score.call_count == 1

    async def test_score_last_turn_only_false_scores_every_turn(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that all turns are scored when score_last_turn_only=False."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=3,
            score_last_turn_only=False,
        )

        # Mock methods
        with patch.object(attack, "_generate_next_prompt_async", new_callable=AsyncMock) as mock_gen:
            with patch.object(attack, "_send_prompt_to_objective_target_async", new_callable=AsyncMock) as mock_send:
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock) as mock_score:
                    mock_gen.return_value = "test prompt"
                    mock_send.return_value = sample_response
                    mock_score.return_value = failure_score

                    context = MultiTurnAttackContext(
                        params=AttackParameters(objective="Test objective"), session=ConversationSession()
                    )

                    await attack._perform_async(context=context)

                    # Should score all 3 turns
                    assert mock_score.call_count == 3

    async def test_score_last_turn_only_runs_all_turns(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that score_last_turn_only runs for exactly max_turns."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=5,
            score_last_turn_only=True,
        )

        # Mock methods
        with patch.object(attack, "_generate_next_prompt_async", new_callable=AsyncMock) as mock_gen:
            with patch.object(attack, "_send_prompt_to_objective_target_async", new_callable=AsyncMock) as mock_send:
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock) as mock_score:
                    mock_gen.return_value = "test prompt"
                    mock_send.return_value = sample_response
                    mock_score.return_value = failure_score

                    context = MultiTurnAttackContext(
                        params=AttackParameters(objective="Test objective"), session=ConversationSession()
                    )

                    result = await attack._perform_async(context=context)

                    # Should have executed all 5 turns
                    assert result.executed_turns == 5
                    # Prompt should have been sent 5 times
                    assert mock_send.call_count == 5

    async def test_score_last_turn_only_sets_intermediate_scores_to_none(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that intermediate turns have last_score set to None."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=3,
            score_last_turn_only=True,
        )

        # Track context.last_score values during execution
        score_values = []

        original_send = AsyncMock(return_value=sample_response)

        async def capture_score(*args, **kwargs):
            context = kwargs.get("context")
            if context:
                score_values.append(context.last_score)
            return await original_send(*args, **kwargs)

        # Mock methods
        with patch.object(attack, "_generate_next_prompt_async", new_callable=AsyncMock) as mock_gen:
            with patch.object(attack, "_send_prompt_to_objective_target_async", side_effect=capture_score):
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock) as mock_score:
                    mock_gen.return_value = "test prompt"
                    mock_score.return_value = failure_score

                    context = MultiTurnAttackContext(
                        params=AttackParameters(objective="Test objective"), session=ConversationSession()
                    )

                    result = await attack._perform_async(context=context)

                    # The final result should have a score
                    assert result.last_score == failure_score

    async def test_score_last_turn_only_can_still_succeed_on_last_turn(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        sample_response: Message,
        success_score: Score,
    ):
        """Test that the attack can still succeed when scoring only the last turn."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            max_turns=3,
            score_last_turn_only=True,
        )

        # Mock methods
        with patch.object(attack, "_generate_next_prompt_async", new_callable=AsyncMock) as mock_gen:
            with patch.object(attack, "_send_prompt_to_objective_target_async", new_callable=AsyncMock) as mock_send:
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock) as mock_score:
                    mock_gen.return_value = "test prompt"
                    mock_send.return_value = sample_response
                    mock_score.return_value = success_score

                    context = MultiTurnAttackContext(
                        params=AttackParameters(objective="Test objective"), session=ConversationSession()
                    )

                    result = await attack._perform_async(context=context)

                    # Should succeed based on final score
                    assert result.outcome == AttackOutcome.SUCCESS
                    assert result.last_score == success_score


@pytest.mark.usefixtures("patch_central_database")
class TestModalityRouterIntegration:
    """Integration tests for the capability-aware modality router wiring."""

    @staticmethod
    def _set_image_capable_objective(target: MagicMock) -> None:
        target.configuration.capabilities.input_modalities = frozenset(
            {frozenset({"text"}), frozenset({"text", "image_path"})}
        )
        target.configuration.capabilities.output_modalities = frozenset({frozenset({"image_path"})})

    @staticmethod
    def _make_image_response() -> Message:
        return Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="/tmp/output.png",
                    original_value_data_type="image_path",
                    converted_value="/tmp/output.png",
                    converted_value_data_type="image_path",
                )
            ]
        )

    async def test_generate_next_prompt_forwards_image_to_adversarial_when_supported(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
    ):
        """Adversarial that advertises {text, image_path} receives a 2-piece feedback Message."""
        self._set_image_capable_objective(mock_objective_target)
        mock_adversarial_chat.configuration.capabilities.input_modalities = frozenset(
            {frozenset({"text"}), frozenset({"text", "image_path"})}
        )

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(
                target=mock_adversarial_chat, adversarial_prompt_template="rationale"
            ),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            prompt_normalizer=mock_prompt_normalizer,
        )

        basic_context.executed_turns = 1
        basic_context.last_response = self._make_image_response()
        mock_prompt_normalizer.send_prompt_async.return_value = _adversarial_reply_message()

        await attack._generate_next_prompt_async(context=basic_context)

        # Adversarial received the multimodal feedback (text + image).
        sent_message = mock_prompt_normalizer.send_prompt_async.call_args.kwargs["message"]
        assert len(sent_message.message_pieces) == 2
        assert sent_message.message_pieces[0].original_value == "rationale"
        assert sent_message.message_pieces[1].original_value_data_type == "image_path"
        assert sent_message.message_pieces[1].original_value == "/tmp/output.png"

    async def test_generate_next_prompt_falls_back_to_text_when_adversarial_lacks_media_input(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
    ):
        """Adversarial with text-only input still sees rationale text, no image piece."""
        self._set_image_capable_objective(mock_objective_target)
        # mock_adversarial_chat default is text-only.

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(
                target=mock_adversarial_chat, adversarial_prompt_template="rationale"
            ),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            prompt_normalizer=mock_prompt_normalizer,
        )

        basic_context.executed_turns = 1
        basic_context.last_response = self._make_image_response()
        mock_prompt_normalizer.send_prompt_async.return_value = _adversarial_reply_message()

        await attack._generate_next_prompt_async(context=basic_context)

        sent_message = mock_prompt_normalizer.send_prompt_async.call_args.kwargs["message"]
        assert len(sent_message.message_pieces) == 1
        assert sent_message.get_value() == "rationale"

    async def test_generate_next_prompt_forwards_prev_image_to_objective_when_supported(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
    ):
        """Objective that accepts {text, image_path} gets the previous image in the request."""
        self._set_image_capable_objective(mock_objective_target)

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            prompt_normalizer=mock_prompt_normalizer,
        )

        basic_context.executed_turns = 1
        basic_context.last_response = self._make_image_response()
        mock_prompt_normalizer.send_prompt_async.return_value = _adversarial_reply_message("adv text")

        result = await attack._generate_next_prompt_async(context=basic_context)

        # Returned message (the one to send to the objective) contains text + previous image.
        assert len(result.message_pieces) == 2
        assert result.message_pieces[0].original_value == "adv text"
        assert result.message_pieces[1].original_value_data_type == "image_path"
        assert result.message_pieces[1].original_value == "/tmp/output.png"

    async def test_generate_next_prompt_does_not_forward_prev_image_when_target_text_only(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
    ):
        """Text-only objective never receives prior images even if available."""
        # mock_objective_target default is text-only.

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            prompt_normalizer=mock_prompt_normalizer,
        )

        basic_context.executed_turns = 1
        basic_context.last_response = self._make_image_response()
        mock_prompt_normalizer.send_prompt_async.return_value = _adversarial_reply_message("adv text")

        result = await attack._generate_next_prompt_async(context=basic_context)

        assert len(result.message_pieces) == 1
        assert result.get_value() == "adv text"

    async def test_generate_next_prompt_placeholder_branch_fills_in_adv_text(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_prompt_normalizer: MagicMock,
        basic_context: MultiTurnAttackContext,
        sample_response: Message,
    ):
        """next_message with placeholder + seed image -> adv text fills the slot."""
        # Edit-only objective: only {text, image_path}.
        mock_objective_target.configuration.capabilities.input_modalities = frozenset(
            {frozenset({"text", "image_path"})}
        )
        mock_adversarial_chat.configuration.capabilities.input_modalities = frozenset(
            {frozenset({"text"}), frozenset({"text", "image_path"})}
        )

        shared_conv = "edit-conv"
        seed_message = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value="",
                    original_value_data_type="text",
                    conversation_id=shared_conv,
                    prompt_metadata={"adversarial_placeholder": True},
                ),
                MessagePiece(
                    role="user",
                    original_value="/path/to/seed.png",
                    original_value_data_type="image_path",
                    conversation_id=shared_conv,
                ),
            ]
        )
        basic_context.next_message = seed_message
        basic_context.executed_turns = 0

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat, first_message="seed text"),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
            prompt_normalizer=mock_prompt_normalizer,
        )

        mock_prompt_normalizer.send_prompt_async.return_value = _adversarial_reply_message("adv text")

        result = await attack._generate_next_prompt_async(context=basic_context)

        # next_message is cleared after consumption.
        assert basic_context.next_message is None
        # Adversarial chat WAS called (placeholder branch routes through it).
        mock_prompt_normalizer.send_prompt_async.assert_called_once()
        sent_prompt_message = mock_prompt_normalizer.send_prompt_async.call_args.kwargs["message"]
        assert len(sent_prompt_message.message_pieces) == 2
        assert sent_prompt_message.message_pieces[0].original_value_data_type == "text"
        assert sent_prompt_message.message_pieces[0].original_value == "seed text"
        assert sent_prompt_message.message_pieces[1].original_value_data_type == "image_path"
        assert sent_prompt_message.message_pieces[1].original_value == "/path/to/seed.png"
        # Resulting message has the adversarial text in place of the placeholder, plus the seed.
        assert len(result.message_pieces) == 2
        assert result.message_pieces[0].original_value == "adv text"
        assert result.message_pieces[0].original_value_data_type == "text"
        assert result.message_pieces[1].original_value_data_type == "image_path"
        assert result.message_pieces[1].original_value == "/path/to/seed.png"

    def test_validate_context_raises_when_edit_only_target_has_no_seed(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: MultiTurnAttackContext,
    ):
        """Edit-only objective without seed in next_message fails validation early."""
        mock_objective_target.configuration.capabilities.input_modalities = frozenset(
            {frozenset({"text", "image_path"})}
        )

        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )

        with pytest.raises(ValueError, match="seed"):
            attack._validate_context(context=basic_context)


class TestRedTeamingAdversarialIdentity:
    """Tests for adversarial config in the RedTeaming attack identity and inline system prompt."""

    def test_get_attack_adversarial_config_includes_target_system_and_seed(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer
    ):
        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )
        config = attack.get_attack_adversarial_config()
        assert config is not None
        assert config.target is mock_adversarial_chat
        assert config.system_prompt is attack._adversarial_chat_system_prompt_template
        assert config.first_message is attack._adversarial_chat_first_message

    def test_identifier_includes_adversarial_chat_child(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer
    ):
        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )
        identifier = attack.get_identifier()
        assert "adversarial_chat" in identifier.children
        assert identifier.children["adversarial_chat"] == mock_adversarial_chat.get_identifier.return_value

    def test_inline_system_prompt_string_resolved_and_in_identity(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer
    ):
        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(
                target=mock_adversarial_chat, system_prompt="custom red team persona"
            ),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )
        assert attack._adversarial_chat_system_prompt_template.value == "custom red team persona"
        assert attack.get_identifier().params["adversarial_system_prompt"] == "custom red team persona"

    def test_inline_seed_prompt_string_used(self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer):
        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(
                target=mock_adversarial_chat, first_message="kick off {{ objective }}"
            ),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )
        assert attack._adversarial_chat_first_message.value == "kick off {{ objective }}"
        assert attack.get_identifier().params["adversarial_seed_prompt"] == "kick off {{ objective }}"

    def test_seed_prompt_none_falls_back_to_default(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer
    ):
        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat, first_message=None),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )
        assert attack._adversarial_chat_first_message.value == DEFAULT_ADVERSARIAL_FIRST_MESSAGE

    def test_get_attack_adversarial_config_returns_none_without_target(
        self, mock_objective_target, mock_adversarial_chat, mock_objective_scorer
    ):
        attack = RedTeamingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
        )
        attack._adversarial_chat = None
        assert attack.get_attack_adversarial_config() is None
