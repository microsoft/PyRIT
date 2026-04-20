# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackParameters,
    AttackScoringConfig,
    SingleTurnAttackContext,
    SkeletonKeyAttack,
)
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
    Score,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import TrueFalseScorer
from tests.unit.mocks import get_mock_scorer_identifier


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
    scorer.score_text_async = AsyncMock()
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
    return SingleTurnAttackContext(
        params=AttackParameters(objective="Test objective"),
        conversation_id=str(uuid.uuid4()),
    )


@pytest.fixture
def sample_response():
    """Create a sample response for testing"""
    return Message(
        message_pieces=[MessagePiece(role="assistant", original_value="Test response", original_value_data_type="text")]
    )


@pytest.fixture
def skeleton_key_response():
    """Create a skeleton key response for testing"""
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="I understand and will comply with your request.",
                original_value_data_type="text",
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
        scorer_class_identifier=get_mock_scorer_identifier(),
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
        scorer_class_identifier=get_mock_scorer_identifier(),
    )


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackInitialization:
    """Test skeleton key attack initialization and configuration."""

    def test_init_with_minimal_required_parameters(self, mock_target):
        """Test initialization with only required parameters."""
        attack = SkeletonKeyAttack(objective_target=mock_target)

        assert attack._objective_target == mock_target
        assert attack._skeleton_key_prompt is not None
        assert isinstance(attack._prompt_normalizer, PromptNormalizer)
        assert attack._max_attempts_on_failure == 0

    def test_init_with_custom_skeleton_key_prompt(self, mock_target):
        """Test initialization with custom skeleton key prompt."""
        custom_prompt = "Custom skeleton key prompt for testing"
        attack = SkeletonKeyAttack(objective_target=mock_target, skeleton_key_prompt=custom_prompt)

        assert attack._skeleton_key_prompt == custom_prompt

    @patch("pyrit.executor.attack.single_turn.skeleton_key.SeedDataset.from_yaml_file")
    def test_init_loads_default_skeleton_key_prompt_when_none_provided(self, mock_dataset, mock_target):
        """Test that default skeleton key prompt is loaded when none is provided."""
        mock_seed_prompt = MagicMock()
        mock_seed_prompt.value = "Default skeleton key prompt"
        mock_dataset.return_value.prompts = [mock_seed_prompt]

        attack = SkeletonKeyAttack(objective_target=mock_target)

        assert attack._skeleton_key_prompt == "Default skeleton key prompt"
        mock_dataset.assert_called_once_with(SkeletonKeyAttack.DEFAULT_SKELETON_KEY_PROMPT_PATH)

    def test_init_with_all_configurations(self, mock_target, mock_true_false_scorer, mock_prompt_normalizer):
        """Test initialization with all configuration options."""
        converter_cfg = AttackConverterConfig()
        scoring_cfg = AttackScoringConfig(objective_scorer=mock_true_false_scorer)
        custom_prompt = "Custom skeleton key"

        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            attack_converter_config=converter_cfg,
            attack_scoring_config=scoring_cfg,
            prompt_normalizer=mock_prompt_normalizer,
            skeleton_key_prompt=custom_prompt,
            max_attempts_on_failure=3,
        )

        assert attack._objective_target == mock_target
        assert attack._skeleton_key_prompt == custom_prompt
        assert attack._prompt_normalizer == mock_prompt_normalizer
        assert attack._max_attempts_on_failure == 3
        assert attack._objective_scorer == mock_true_false_scorer

    def test_default_skeleton_key_prompt_path_exists(self):
        """Test that the default skeleton key prompt path is correctly set."""
        expected_path = Path("pyrit/datasets/executors/skeleton_key/skeleton_key.prompt")
        assert str(SkeletonKeyAttack.DEFAULT_SKELETON_KEY_PROMPT_PATH).endswith(str(expected_path))


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyPromptLoading:
    """Test skeleton key prompt loading logic."""

    def test_load_skeleton_key_prompt_with_custom_prompt(self, mock_target):
        """Test loading skeleton key prompt with custom string."""
        custom_prompt = "Test custom skeleton key prompt"
        attack = SkeletonKeyAttack(objective_target=mock_target)

        result = attack._load_skeleton_key_prompt(custom_prompt)

        assert result == custom_prompt

    @patch("pyrit.executor.attack.single_turn.skeleton_key.SeedDataset.from_yaml_file")
    def test_load_skeleton_key_prompt_from_default_file(self, mock_dataset, mock_target):
        """Test loading skeleton key prompt from default file."""
        mock_seed_prompt = MagicMock()
        mock_seed_prompt.value = "Default prompt from file"
        mock_dataset.return_value.prompts = [mock_seed_prompt]

        # Create attack with custom prompt to avoid calling dataset loading in __init__
        attack = SkeletonKeyAttack(objective_target=mock_target, skeleton_key_prompt="temp")

        # Now test the method directly
        result = attack._load_skeleton_key_prompt(None)

        assert result == "Default prompt from file"
        mock_dataset.assert_called_once_with(SkeletonKeyAttack.DEFAULT_SKELETON_KEY_PROMPT_PATH)

    @patch("pyrit.executor.attack.single_turn.skeleton_key.SeedDataset.from_yaml_file")
    def test_load_skeleton_key_prompt_handles_empty_string(self, mock_dataset, mock_target):
        """Test that empty string triggers loading from default file."""
        mock_seed_prompt = MagicMock()
        mock_seed_prompt.value = "Default prompt"
        mock_dataset.return_value.prompts = [mock_seed_prompt]

        # Create attack with custom prompt to avoid calling dataset loading in __init__
        attack = SkeletonKeyAttack(objective_target=mock_target, skeleton_key_prompt="temp")

        # Now test the method directly
        result = attack._load_skeleton_key_prompt("")

        assert result == "Default prompt"
        mock_dataset.assert_called_once()


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyPromptSending:
    """Test skeleton key prompt sending functionality."""

    @pytest.mark.asyncio
    async def test_send_skeleton_key_prompt_successful(
        self, mock_target, mock_prompt_normalizer, basic_context, skeleton_key_response
    ):
        """Test successful sending of skeleton key prompt."""
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            prompt_normalizer=mock_prompt_normalizer,
            skeleton_key_prompt="Test skeleton key",
        )

        mock_prompt_normalizer.send_prompt_async.return_value = skeleton_key_response

        result = await attack._send_skeleton_key_prompt_async(context=basic_context)

        assert result == skeleton_key_response

        # Verify the prompt normalizer was called with correct parameters
        call_args = mock_prompt_normalizer.send_prompt_async.call_args
        assert call_args.kwargs["target"] == mock_target
        assert call_args.kwargs["conversation_id"] == basic_context.conversation_id

        # Check that skeleton key prompt was included in message
        message = call_args.kwargs["message"]
        assert isinstance(message, Message)
        assert len(message.message_pieces) == 1
        assert message.message_pieces[0].original_value == "Test skeleton key"
        assert message.message_pieces[0].original_value_data_type == "text"

    @pytest.mark.asyncio
    async def test_send_skeleton_key_prompt_filtered_response(self, mock_target, mock_prompt_normalizer, basic_context):
        """Test handling of filtered skeleton key prompt response."""
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            prompt_normalizer=mock_prompt_normalizer,
            skeleton_key_prompt="Test skeleton key",
        )

        # Simulate filtered response
        mock_prompt_normalizer.send_prompt_async.return_value = None

        result = await attack._send_skeleton_key_prompt_async(context=basic_context)

        assert result is None

    @pytest.mark.asyncio
    async def test_send_skeleton_key_prompt_uses_correct_converters(
        self, mock_target, mock_prompt_normalizer, basic_context
    ):
        """Test that skeleton key prompt sending uses correct converter configurations."""
        from pyrit.prompt_normalizer.prompt_converter_configuration import (
            PromptConverterConfiguration,
        )

        request_converters = [PromptConverterConfiguration(converters=[])]
        response_converters = [PromptConverterConfiguration(converters=[])]

        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            prompt_normalizer=mock_prompt_normalizer,
            attack_converter_config=AttackConverterConfig(
                request_converters=request_converters, response_converters=response_converters
            ),
            skeleton_key_prompt="Test skeleton key",
        )

        mock_prompt_normalizer.send_prompt_async.return_value = MagicMock()
        basic_context.memory_labels = {"test": "label"}

        await attack._send_skeleton_key_prompt_async(context=basic_context)

        call_args = mock_prompt_normalizer.send_prompt_async.call_args
        assert call_args.kwargs["request_converter_configurations"] == request_converters
        assert call_args.kwargs["response_converter_configurations"] == response_converters
        assert call_args.kwargs["labels"] == {"test": "label"}
        assert "attack_identifier" in call_args.kwargs


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyFailureResult:
    """Test skeleton key failure result creation."""

    def test_create_skeleton_key_failure_result(self, mock_target, basic_context):
        """Test creation of failure result when skeleton key prompt fails."""
        attack = SkeletonKeyAttack(objective_target=mock_target)

        result = attack._create_skeleton_key_failure_result(context=basic_context)

        assert isinstance(result, AttackResult)
        assert result.conversation_id == basic_context.conversation_id
        assert result.objective == basic_context.objective
        assert result.outcome == AttackOutcome.FAILURE
        assert result.outcome_reason == "Skeleton key prompt was filtered or failed"
        assert result.executed_turns == 1
        assert result.last_response is None
        assert result.last_score is None
        assert result.attack_identifier == attack.get_identifier()


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackExecution:
    """Test main skeleton key attack execution logic."""

    @pytest.mark.asyncio
    async def test_perform_attack_skeleton_key_success_objective_success(
        self, mock_target, mock_true_false_scorer, basic_context, skeleton_key_response, sample_response, success_score
    ):
        """Test complete successful attack flow: skeleton key succeeds, objective succeeds."""
        attack_scoring_config = AttackScoringConfig(objective_scorer=mock_true_false_scorer)
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            attack_scoring_config=attack_scoring_config,
            skeleton_key_prompt="Test skeleton key",
        )

        # Mock skeleton key prompt sending
        with patch.object(
            attack, "_send_skeleton_key_prompt_async", return_value=skeleton_key_response
        ) as mock_skeleton:
            # Mock parent class attack execution
            with patch.object(attack.__class__.__bases__[0], "_perform_async") as mock_parent:
                mock_parent.return_value = AttackResult(
                    conversation_id=basic_context.conversation_id,
                    objective=basic_context.objective,
                    attack_identifier=attack.get_identifier(),
                    last_response=sample_response,
                    last_score=success_score,
                    outcome=AttackOutcome.SUCCESS,
                    executed_turns=1,
                )

                result = await attack._perform_async(context=basic_context)

                # Verify skeleton key was sent
                mock_skeleton.assert_called_once_with(context=basic_context)

                # Verify parent attack was called
                mock_parent.assert_called_once_with(context=basic_context)

                # Verify result properties
                assert result.outcome == AttackOutcome.SUCCESS
                assert result.executed_turns == 2  # Should be updated to 2 turns
                assert result.last_response == sample_response
                assert result.last_score == success_score

    @pytest.mark.asyncio
    async def test_perform_attack_skeleton_key_failure(self, mock_target, basic_context):
        """Test attack flow when skeleton key prompt is filtered."""
        attack = SkeletonKeyAttack(objective_target=mock_target, skeleton_key_prompt="Test skeleton key")

        # Mock skeleton key prompt failure
        with patch.object(attack, "_send_skeleton_key_prompt_async", return_value=None) as mock_skeleton:
            with patch.object(attack, "_create_skeleton_key_failure_result") as mock_failure:
                expected_result = AttackResult(
                    conversation_id=basic_context.conversation_id,
                    objective=basic_context.objective,
                    attack_identifier=attack.get_identifier(),
                    outcome=AttackOutcome.FAILURE,
                    outcome_reason="Skeleton key prompt was filtered or failed",
                    executed_turns=1,
                )
                mock_failure.return_value = expected_result

                result = await attack._perform_async(context=basic_context)

                # Verify skeleton key was attempted
                mock_skeleton.assert_called_once_with(context=basic_context)

                # Verify failure result was created
                mock_failure.assert_called_once_with(context=basic_context)

                # Verify result is the failure result
                assert result == expected_result

    @pytest.mark.asyncio
    async def test_perform_attack_skeleton_key_success_objective_failure(
        self, mock_target, mock_true_false_scorer, basic_context, skeleton_key_response, sample_response, failure_score
    ):
        """Test attack flow: skeleton key succeeds but objective fails."""
        attack_scoring_config = AttackScoringConfig(objective_scorer=mock_true_false_scorer)
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            attack_scoring_config=attack_scoring_config,
            skeleton_key_prompt="Test skeleton key",
        )

        # Mock skeleton key prompt success
        with patch.object(attack, "_send_skeleton_key_prompt_async", return_value=skeleton_key_response):
            # Mock parent class attack execution with failure
            with patch.object(attack.__class__.__bases__[0], "_perform_async") as mock_parent:
                mock_parent.return_value = AttackResult(
                    conversation_id=basic_context.conversation_id,
                    objective=basic_context.objective,
                    attack_identifier=attack.get_identifier(),
                    last_response=sample_response,
                    last_score=failure_score,
                    outcome=AttackOutcome.FAILURE,
                    executed_turns=1,
                )

                result = await attack._perform_async(context=basic_context)

                # Verify result shows overall failure but 2 turns were executed
                assert result.outcome == AttackOutcome.FAILURE
                assert result.executed_turns == 2
                assert result.last_score == failure_score


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackStateMangement:
    """Test skeleton key attack state management."""

    @pytest.mark.asyncio
    async def test_attack_state_isolation_between_executions(self, mock_target):
        """Test that attacks don't share state between executions."""
        attack = SkeletonKeyAttack(objective_target=mock_target)

        # Create multiple contexts
        context1 = SingleTurnAttackContext(
            params=AttackParameters(objective="Objective 1"),
            conversation_id=str(uuid.uuid4()),
        )
        context2 = SingleTurnAttackContext(
            params=AttackParameters(objective="Objective 2"),
            conversation_id=str(uuid.uuid4()),
        )

        # Mock skeleton key prompt to return None (filtered)
        with patch.object(attack, "_send_skeleton_key_prompt_async", return_value=None):
            result1 = await attack._perform_async(context=context1)
            result2 = await attack._perform_async(context=context2)

        # Verify state isolation
        assert result1.conversation_id != result2.conversation_id
        assert result1.objective != result2.objective
        assert result1.conversation_id == context1.conversation_id
        assert result2.conversation_id == context2.conversation_id


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackParameterValidation:
    """Test skeleton key attack parameter validation."""

    def test_init_validates_skeleton_key_prompt_type(self, mock_target):
        """Test that skeleton key prompt must be string or None."""
        # Valid cases
        SkeletonKeyAttack(objective_target=mock_target, skeleton_key_prompt=None)
        SkeletonKeyAttack(objective_target=mock_target, skeleton_key_prompt="Valid string")
        SkeletonKeyAttack(objective_target=mock_target, skeleton_key_prompt="")

    def test_skeleton_key_attack_inherits_parent_validation(self, mock_target):
        """Test that skeleton key attack inherits parent class validation."""
        # Test that it validates max_attempts_on_failure like parent
        with pytest.raises(ValueError):
            SkeletonKeyAttack(objective_target=mock_target, max_attempts_on_failure=-1)


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackParamsType:
    """Tests for params_type in SkeletonKeyAttack"""

    def test_params_type_excludes_next_message(self, mock_target):
        """Test that params_type excludes next_message field."""
        import dataclasses

        attack = SkeletonKeyAttack(objective_target=mock_target)
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "next_message" not in fields

    def test_params_type_excludes_prepended_conversation(self, mock_target):
        """Test that params_type excludes prepended_conversation field."""
        import dataclasses

        attack = SkeletonKeyAttack(objective_target=mock_target)
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "prepended_conversation" not in fields

    def test_params_type_includes_objective(self, mock_target):
        """Test that params_type includes objective field."""
        import dataclasses

        attack = SkeletonKeyAttack(objective_target=mock_target)
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "objective" in fields
