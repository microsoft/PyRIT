# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackScoringConfig,
    ConversationSession,
    ConversationState,
    MultiPromptSendingAttack,
    MultiPromptSendingAttackParameters,
    MultiTurnAttackContext,
)
from pyrit.identifiers import ScorerIdentifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
    Score,
)
from pyrit.prompt_converter import Base64Converter, StringJoinConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import Scorer, TrueFalseScorer


def _mock_scorer_id(name: str = "MockScorer") -> ScorerIdentifier:
    """Helper to create ScorerIdentifier for tests."""
    return ScorerIdentifier(
        class_name=name,
        class_module="test_module",
        class_description="",
        identifier_type="instance",
    )


@pytest.fixture
def mock_target():
    """Create a mock prompt target for testing"""
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.get_identifier.return_value = {"id": "mock_target_id"}
    return target


@pytest.fixture
def mock_true_false_scorer():
    """Create a mock true/false scorer for testing"""
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock()
    return scorer


@pytest.fixture
def mock_non_true_false_scorer():
    """Create a mock scorer that is not a true/false type"""
    scorer = MagicMock(spec=Scorer)
    return scorer


@pytest.fixture
def mock_prompt_normalizer():
    """Create a mock prompt normalizer for testing"""
    normalizer = MagicMock(spec=PromptNormalizer)
    normalizer.send_prompt_async = AsyncMock()
    return normalizer


@pytest.fixture
def basic_context():
    """Create a basic context for testing"""
    return MultiTurnAttackContext(
        params=MultiPromptSendingAttackParameters(
            objective="Test objective",
            user_messages=[
                Message.from_prompt(prompt="First prompt", role="user"),
                Message.from_prompt(prompt="Second prompt", role="user"),
                Message.from_prompt(prompt="Third prompt", role="user"),
            ],
        ),
        session=ConversationSession(),
    )


@pytest.fixture
def sample_response():
    """Create a sample response for testing"""
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
def success_score():
    """Create a success score for testing"""
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
def failure_score():
    """Create a failure score for testing"""
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


@pytest.mark.usefixtures("patch_central_database")
class TestMultiPromptSendingAttackInitialization:
    """Tests for MultiPromptSendingAttack initialization and configuration"""

    def test_init_with_minimal_required_parameters(self, mock_target):
        attack = MultiPromptSendingAttack(objective_target=mock_target)

        assert attack._objective_target == mock_target
        assert attack._objective_scorer is None
        assert isinstance(attack._prompt_normalizer, PromptNormalizer)

    def test_init_with_valid_true_false_scorer(self, mock_target, mock_true_false_scorer):
        attack_scoring_config = AttackScoringConfig(objective_scorer=mock_true_false_scorer)
        attack = MultiPromptSendingAttack(objective_target=mock_target, attack_scoring_config=attack_scoring_config)

        assert attack._objective_scorer == mock_true_false_scorer

    def test_init_with_all_custom_configurations(self, mock_target, mock_true_false_scorer, mock_prompt_normalizer):
        converter_cfg = AttackConverterConfig(
            request_converters=[Base64Converter()], response_converters=[StringJoinConverter()]
        )
        scoring_cfg = AttackScoringConfig(objective_scorer=mock_true_false_scorer)

        attack = MultiPromptSendingAttack(
            objective_target=mock_target,
            attack_converter_config=converter_cfg,
            attack_scoring_config=scoring_cfg,
            prompt_normalizer=mock_prompt_normalizer,
        )

        assert attack._request_converters == converter_cfg.request_converters
        assert attack._response_converters == converter_cfg.response_converters
        assert attack._objective_scorer == mock_true_false_scorer
        assert attack._prompt_normalizer == mock_prompt_normalizer

    def test_conversation_manager_initialized_correctly(self, mock_target):
        attack = MultiPromptSendingAttack(objective_target=mock_target)

        assert attack._conversation_manager is not None
        assert hasattr(attack._conversation_manager, "initialize_context_async")

    def test_get_objective_target_returns_correct_target(self, mock_target):
        """Test that get_objective_target returns the target passed during initialization."""
        attack = MultiPromptSendingAttack(objective_target=mock_target)

        assert attack.get_objective_target() == mock_target

    def test_get_attack_scoring_config_returns_config(self, mock_target, mock_true_false_scorer):
        """Test that get_attack_scoring_config returns the configured AttackScoringConfig."""
        scoring_config = AttackScoringConfig(
            objective_scorer=mock_true_false_scorer,
            auxiliary_scorers=[MagicMock(spec=Scorer)],
        )

        attack = MultiPromptSendingAttack(objective_target=mock_target, attack_scoring_config=scoring_config)

        result = attack.get_attack_scoring_config()

        assert result.objective_scorer == mock_true_false_scorer
        assert len(result.auxiliary_scorers) == 1


@pytest.mark.usefixtures("patch_central_database")
class TestContextValidation:
    """Tests for context validation logic"""

    def test_validate_context_raises_for_empty_objective(self, mock_target):
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        context = MultiTurnAttackContext(
            params=MultiPromptSendingAttackParameters(
                objective="",
                user_messages=[Message.from_prompt(prompt="prompt1", role="user")],
            ),
            session=ConversationSession(),
        )

        with pytest.raises(ValueError, match="Attack objective must be provided and non-empty in the context"):
            attack._validate_context(context=context)

    def test_validate_context_raises_for_whitespace_objective(self, mock_target):
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        context = MultiTurnAttackContext(
            params=MultiPromptSendingAttackParameters(
                objective="   ",
                user_messages=[Message.from_prompt(prompt="prompt1", role="user")],
            ),
            session=ConversationSession(),
        )

        with pytest.raises(ValueError, match="Attack objective must be provided and non-empty in the context"):
            attack._validate_context(context=context)

    def test_validate_context_raises_for_empty_messages(self, mock_target):
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        context = MultiTurnAttackContext(
            params=MultiPromptSendingAttackParameters(
                objective="Valid objective",
                user_messages=[],
            ),
            session=ConversationSession(),
        )

        with pytest.raises(ValueError, match="User messages must be provided and non-empty in the params"):
            attack._validate_context(context=context)

    def test_validate_context_with_complete_valid_context(self, mock_target, basic_context):
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        attack._validate_context(context=basic_context)  # Should not raise


@pytest.mark.usefixtures("patch_central_database")
class TestSetupPhase:
    """Tests for the setup phase of the attack"""

    @pytest.mark.asyncio
    async def test_setup_initializes_conversation_session(self, mock_target, basic_context):
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        basic_context.session = None

        # Mock conversation manager
        mock_state = ConversationState(turn_count=0)
        with patch.object(attack._conversation_manager, "initialize_context_async", return_value=mock_state):
            await attack._setup_async(context=basic_context)

        assert basic_context.session is not None
        assert isinstance(basic_context.session, ConversationSession)

    @pytest.mark.asyncio
    async def test_setup_merges_memory_labels_correctly(self, mock_target, basic_context):
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        attack._memory_labels = {"strategy_label": "strategy_value", "common": "strategy"}
        basic_context.memory_labels = {"context_label": "context_value", "common": "context"}

        # Mock that simulates initialize_context_async merging labels
        async def mock_initialize(*, context, memory_labels=None, **kwargs):
            from pyrit.common.utils import combine_dict

            context.memory_labels = combine_dict(existing_dict=memory_labels, new_dict=context.memory_labels)
            return ConversationState(turn_count=0)

        with patch.object(attack._conversation_manager, "initialize_context_async", side_effect=mock_initialize):
            await attack._setup_async(context=basic_context)

        assert basic_context.memory_labels == {
            "strategy_label": "strategy_value",
            "context_label": "context_value",
            "common": "context",
        }

    @pytest.mark.asyncio
    async def test_setup_updates_conversation_state_with_converters(self, mock_target, basic_context):
        converter_config = [PromptConverterConfiguration(converters=[])]
        attack = MultiPromptSendingAttack(
            objective_target=mock_target,
            attack_converter_config=AttackConverterConfig(request_converters=converter_config),
        )

        with patch.object(attack._conversation_manager, "initialize_context_async") as mock_update:
            await attack._setup_async(context=basic_context)

            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args[1]["context"] == basic_context
            assert call_args[1]["target"] == mock_target
            assert call_args[1]["conversation_id"] == basic_context.session.conversation_id
            assert call_args[1]["request_converters"] == attack._request_converters


@pytest.mark.usefixtures("patch_central_database")
class TestPromptSending:
    """Tests for sending prompts to target"""

    @pytest.mark.asyncio
    async def test_send_prompt_to_target_with_all_configurations(
        self, mock_target, mock_prompt_normalizer, basic_context, sample_response
    ):
        request_converters = [PromptConverterConfiguration(converters=[])]
        response_converters = [PromptConverterConfiguration(converters=[])]

        attack = MultiPromptSendingAttack(
            objective_target=mock_target,
            prompt_normalizer=mock_prompt_normalizer,
            attack_converter_config=AttackConverterConfig(
                request_converters=request_converters, response_converters=response_converters
            ),
        )

        test_message = Message.from_prompt(prompt="test prompt", role="user")
        mock_prompt_normalizer.send_prompt_async.return_value = sample_response

        result = await attack._send_prompt_to_objective_target_async(
            current_message=test_message, context=basic_context
        )

        assert result == sample_response
        mock_prompt_normalizer.send_prompt_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_prompt_handles_none_response(self, mock_target, mock_prompt_normalizer, basic_context):
        mock_prompt_normalizer.send_prompt_async.return_value = None

        attack = MultiPromptSendingAttack(objective_target=mock_target, prompt_normalizer=mock_prompt_normalizer)

        test_message = Message.from_prompt(prompt="test prompt", role="user")

        result = await attack._send_prompt_to_objective_target_async(
            current_message=test_message, context=basic_context
        )

        assert result is None


@pytest.mark.usefixtures("patch_central_database")
class TestResponseEvaluation:
    """Tests for response evaluation logic"""

    @pytest.mark.asyncio
    async def test_evaluate_response_with_objective_scorer_returns_score(
        self, mock_target, mock_true_false_scorer, sample_response, success_score
    ):
        attack_scoring_config = AttackScoringConfig(objective_scorer=mock_true_false_scorer)
        attack = MultiPromptSendingAttack(objective_target=mock_target, attack_scoring_config=attack_scoring_config)

        with patch("pyrit.score.Scorer.score_response_async") as mock_score:
            mock_score.return_value = {"objective_scores": [success_score]}

            result = await attack._evaluate_response_async(response=sample_response, objective="test objective")

            assert result == success_score
            mock_score.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_response_without_objective_scorer_returns_none(self, mock_target, sample_response):
        attack = MultiPromptSendingAttack(objective_target=mock_target)

        result = await attack._evaluate_response_async(response=sample_response, objective="test objective")

        assert result is None

    @pytest.mark.asyncio
    async def test_evaluate_response_with_auxiliary_scorers(
        self, mock_target, mock_true_false_scorer, sample_response, success_score
    ):
        auxiliary_scorer = MagicMock(spec=Scorer)

        attack_scoring_config = AttackScoringConfig(
            objective_scorer=mock_true_false_scorer, auxiliary_scorers=[auxiliary_scorer]
        )
        attack = MultiPromptSendingAttack(objective_target=mock_target, attack_scoring_config=attack_scoring_config)

        with patch("pyrit.score.Scorer.score_response_async") as mock_score:
            mock_score.return_value = {"objective_scores": [success_score]}

            result = await attack._evaluate_response_async(response=sample_response, objective="test objective")

            # Verify the call included auxiliary scorers
            call_args = mock_score.call_args[1]
            assert call_args["auxiliary_scorers"] == [auxiliary_scorer]
            assert result == success_score


@pytest.mark.usefixtures("patch_central_database")
class TestAttackExecution:
    """Tests for the main attack execution logic"""

    @pytest.mark.asyncio
    async def test_perform_async_sends_all_prompts_in_sequence(
        self, mock_target, mock_prompt_normalizer, basic_context, sample_response
    ):
        mock_prompt_normalizer.send_prompt_async.return_value = sample_response

        attack = MultiPromptSendingAttack(objective_target=mock_target, prompt_normalizer=mock_prompt_normalizer)

        result = await attack._perform_async(context=basic_context)

        # Should have called send_prompt_async for each message in sequence
        assert mock_prompt_normalizer.send_prompt_async.call_count == len(basic_context.params.user_messages)
        assert result.executed_turns == len(basic_context.params.user_messages)
        assert result.last_response is not None

    @pytest.mark.asyncio
    async def test_perform_async_stops_on_failed_prompt(self, mock_target, mock_prompt_normalizer, basic_context):
        # First prompt succeeds, second fails
        mock_prompt_normalizer.send_prompt_async.side_effect = [
            Message(
                message_pieces=[
                    MessagePiece(role="assistant", original_value="response1", original_value_data_type="text")
                ]
            ),
            None,  # Failed prompt
            Message(
                message_pieces=[
                    MessagePiece(role="assistant", original_value="response3", original_value_data_type="text")
                ]
            ),
        ]

        attack = MultiPromptSendingAttack(objective_target=mock_target, prompt_normalizer=mock_prompt_normalizer)

        result = await attack._perform_async(context=basic_context)

        # Should have stopped after the failed prompt
        assert mock_prompt_normalizer.send_prompt_async.call_count == 2
        assert result.executed_turns == 1  # Only first prompt succeeded

    @pytest.mark.asyncio
    async def test_perform_async_evaluates_final_response(
        self, mock_target, mock_true_false_scorer, mock_prompt_normalizer, basic_context, sample_response, success_score
    ):
        mock_prompt_normalizer.send_prompt_async.return_value = sample_response
        attack_scoring_config = AttackScoringConfig(objective_scorer=mock_true_false_scorer)

        attack = MultiPromptSendingAttack(
            objective_target=mock_target,
            prompt_normalizer=mock_prompt_normalizer,
            attack_scoring_config=attack_scoring_config,
        )

        with patch.object(attack, "_evaluate_response_async", return_value=success_score) as mock_evaluate:
            result = await attack._perform_async(context=basic_context)

            mock_evaluate.assert_called_once_with(response=sample_response, objective=basic_context.objective)
            assert result.last_score == success_score


@pytest.mark.usefixtures("patch_central_database")
class TestDetermineAttackOutcome:
    """Tests for the _determine_attack_outcome method"""

    def test_determine_attack_outcome_success_with_true_string_score(
        self, mock_target, sample_response, success_score, basic_context
    ):
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        attack._objective_scorer = MagicMock()

        outcome, reason = attack._determine_attack_outcome(
            response=sample_response, score=success_score, context=basic_context
        )

        assert outcome == AttackOutcome.SUCCESS
        assert reason == "Objective achieved according to scorer"

    def test_determine_attack_outcome_failure_with_false_string_score(
        self, mock_target, sample_response, failure_score, basic_context
    ):
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        attack._objective_scorer = MagicMock()

        outcome, reason = attack._determine_attack_outcome(
            response=sample_response, score=failure_score, context=basic_context
        )

        assert outcome == AttackOutcome.FAILURE
        assert reason == "Failed to achieve objective"

    def test_determine_attack_outcome_no_scorer(self, mock_target, sample_response, basic_context):
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        attack._objective_scorer = None

        outcome, reason = attack._determine_attack_outcome(response=sample_response, score=None, context=basic_context)

        assert outcome == AttackOutcome.UNDETERMINED
        assert reason == "No objective scorer configured"

    def test_determine_attack_outcome_no_response(self, mock_target, basic_context):
        """
        Test failure outcome when no response is received.
        This handles cases where all prompts were filtered or failed.
        """
        attack = MultiPromptSendingAttack(objective_target=mock_target)
        attack._objective_scorer = MagicMock()

        outcome, reason = attack._determine_attack_outcome(response=None, score=None, context=basic_context)

        assert outcome == AttackOutcome.FAILURE
        assert reason == "At least one prompt was filtered or failed to get a response"


@pytest.mark.usefixtures("patch_central_database")
class TestExecuteAsync:
    """Tests for the execute_async method (main entry point)"""

    @pytest.mark.asyncio
    async def test_execute_async_with_valid_parameters(self, mock_target):
        attack = MultiPromptSendingAttack(objective_target=mock_target)

        with patch.object(attack, "_perform_async") as mock_perform:
            mock_perform.return_value = AttackResult(
                conversation_id=str(uuid.uuid4()),
                objective="test",
                attack_identifier=attack.get_identifier(),
                outcome=AttackOutcome.UNDETERMINED,
                outcome_reason="test",
                executed_turns=0,
            )

            result = await attack.execute_async(
                objective="Test objective",
                user_messages=[
                    Message.from_prompt(prompt="prompt1", role="user"),
                    Message.from_prompt(prompt="prompt2", role="user"),
                ],
            )

            assert isinstance(result, AttackResult)
            mock_perform.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_async_validates_messages_parameter(self, mock_target):
        """
        Test that execute_async validates the user_messages parameter.
        This ensures the required parameter is properly validated at the entry point.
        """
        attack = MultiPromptSendingAttack(objective_target=mock_target)

        with pytest.raises(ValueError):
            await attack.execute_async(
                objective="Test objective"
                # Missing user_messages parameter
            )

    @pytest.mark.asyncio
    async def test_execute_async_passes_messages_to_context(self, mock_target):
        """
        Test that execute_async properly passes user_messages to the context.
        This verifies parameter propagation through the execution pipeline.
        """
        attack = MultiPromptSendingAttack(objective_target=mock_target)

        test_messages = [
            Message.from_prompt(prompt="prompt1", role="user"),
            Message.from_prompt(prompt="prompt2", role="user"),
            Message.from_prompt(prompt="prompt3", role="user"),
        ]

        with patch.object(attack, "_perform_async") as mock_perform:
            mock_perform.return_value = AttackResult(
                conversation_id=str(uuid.uuid4()),
                objective="test",
                attack_identifier=attack.get_identifier(),
                outcome=AttackOutcome.UNDETERMINED,
                outcome_reason="test",
                executed_turns=0,
            )

            await attack.execute_async(objective="Test objective", user_messages=test_messages)

            # Verify the context was created with the correct messages
            call_args = mock_perform.call_args[1]
            context = call_args["context"]
            assert context.params.user_messages == test_messages


@pytest.mark.usefixtures("patch_central_database")
class TestConverterIntegration:
    """Tests for converter integration"""

    @pytest.mark.asyncio
    async def test_perform_attack_with_converters(
        self, mock_target, mock_prompt_normalizer, basic_context, sample_response
    ):
        converter_config = AttackConverterConfig(request_converters=[Base64Converter()])
        mock_prompt_normalizer.send_prompt_async.return_value = sample_response

        attack = MultiPromptSendingAttack(
            objective_target=mock_target,
            attack_converter_config=converter_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        await attack._perform_async(context=basic_context)

        # Verify that send_prompt_async was called with the converter config
        call_args = mock_prompt_normalizer.send_prompt_async.call_args[1]
        assert call_args["request_converter_configurations"] == converter_config.request_converters

    @pytest.mark.asyncio
    async def test_perform_attack_with_response_converters(
        self, mock_target, mock_prompt_normalizer, basic_context, sample_response
    ):
        converter_config = AttackConverterConfig(response_converters=[StringJoinConverter()])
        mock_prompt_normalizer.send_prompt_async.return_value = sample_response

        attack = MultiPromptSendingAttack(
            objective_target=mock_target,
            attack_converter_config=converter_config,
            prompt_normalizer=mock_prompt_normalizer,
        )

        await attack._perform_async(context=basic_context)

        # Verify that send_prompt_async was called with the converter config
        call_args = mock_prompt_normalizer.send_prompt_async.call_args[1]
        assert call_args["response_converter_configurations"] == converter_config.response_converters


@pytest.mark.usefixtures("patch_central_database")
class TestEdgeCasesAndErrorHandling:
    """Tests for edge cases and error handling scenarios"""

    @pytest.mark.asyncio
    async def test_perform_attack_with_empty_messages(
        self, mock_target, mock_prompt_normalizer, mock_true_false_scorer, basic_context
    ):
        mock_prompt_normalizer.send_prompt_async.return_value = None

        attack_scoring_config = AttackScoringConfig(objective_scorer=mock_true_false_scorer)

        attack = MultiPromptSendingAttack(
            objective_target=mock_target,
            prompt_normalizer=mock_prompt_normalizer,
            attack_scoring_config=attack_scoring_config,
        )

        result = await attack._perform_async(context=basic_context)

        assert result.executed_turns == 0
        assert result.last_response is None
        assert result.outcome == AttackOutcome.FAILURE

    @pytest.mark.asyncio
    async def test_perform_attack_with_single_prompt(self, mock_target, mock_prompt_normalizer, sample_response):
        mock_prompt_normalizer.send_prompt_async.return_value = sample_response

        attack = MultiPromptSendingAttack(objective_target=mock_target, prompt_normalizer=mock_prompt_normalizer)

        context = MultiTurnAttackContext(
            params=MultiPromptSendingAttackParameters(
                objective="Test objective",
                user_messages=[Message.from_prompt(prompt="Single prompt", role="user")],
            ),
            session=ConversationSession(),
        )

        result = await attack._perform_async(context=context)

        assert result.executed_turns == 1
        assert result.last_response is not None
        assert mock_prompt_normalizer.send_prompt_async.call_count == 1

    def test_attack_has_unique_identifier(self, mock_target):
        attack1 = MultiPromptSendingAttack(objective_target=mock_target)
        attack2 = MultiPromptSendingAttack(objective_target=mock_target)

        assert attack1.get_identifier() != attack2.get_identifier()

    @pytest.mark.asyncio
    async def test_teardown_async_is_noop(self, mock_target, basic_context):
        attack = MultiPromptSendingAttack(objective_target=mock_target)

        # Should complete without error
        await attack._teardown_async(context=basic_context)
        # No assertions needed - we just want to ensure it runs without exceptions
