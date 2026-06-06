# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_scorer_identifier, get_mock_target_identifier

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


@pytest.fixture
def mock_target():
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    return target


@pytest.fixture
def mock_true_false_scorer():
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_text_async = AsyncMock()
    scorer.get_identifier.return_value = get_mock_scorer_identifier()
    return scorer


@pytest.fixture
def mock_prompt_normalizer():
    normalizer = MagicMock(spec=PromptNormalizer)
    normalizer.send_prompt_async = AsyncMock()
    return normalizer


@pytest.fixture
def basic_context():
    return SingleTurnAttackContext(
        params=AttackParameters(objective="Test objective"),
        conversation_id=str(uuid.uuid4()),
    )


@pytest.fixture
def sample_response():
    return Message(
        message_pieces=[MessagePiece(role="assistant", original_value="Test response", original_value_data_type="text")]
    )


@pytest.fixture
def success_score():
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
        attack = SkeletonKeyAttack(objective_target=mock_target)

        assert attack._objective_target == mock_target
        assert attack._skeleton_key_prompt is not None
        assert attack._skeleton_key_acceptance is not None
        assert isinstance(attack._prompt_normalizer, PromptNormalizer)
        assert attack._max_attempts_on_failure == 0

    def test_init_with_custom_skeleton_key_prompt(self, mock_target):
        custom_prompt = "Custom skeleton key prompt for testing"
        attack = SkeletonKeyAttack(objective_target=mock_target, skeleton_key_prompt=custom_prompt)

        assert attack._skeleton_key_prompt == custom_prompt

    def test_init_with_custom_skeleton_key_acceptance(self, mock_target):
        custom_acceptance = "Custom acceptance response for testing"
        attack = SkeletonKeyAttack(objective_target=mock_target, skeleton_key_acceptance=custom_acceptance)

        assert attack._skeleton_key_acceptance == custom_acceptance

    def test_init_preserves_empty_string_overrides(self, mock_target):
        """Empty strings are intentional overrides, not 'use the default'."""
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="",
            skeleton_key_acceptance="",
        )

        assert attack._skeleton_key_prompt == ""
        assert attack._skeleton_key_acceptance == ""

    @patch("pyrit.executor.attack.single_turn.skeleton_key.SeedDataset.from_yaml_file")
    def test_init_loads_defaults_from_files_when_none_provided(self, mock_dataset, mock_target):
        mock_seed_prompt = MagicMock()
        mock_seed_prompt.value = "Default value"
        mock_dataset.return_value.prompts = [mock_seed_prompt]

        attack = SkeletonKeyAttack(objective_target=mock_target)

        assert attack._skeleton_key_prompt == "Default value"
        assert attack._skeleton_key_acceptance == "Default value"
        assert mock_dataset.call_count == 2
        mock_dataset.assert_any_call(SkeletonKeyAttack.DEFAULT_SKELETON_KEY_PROMPT_PATH)
        mock_dataset.assert_any_call(SkeletonKeyAttack.DEFAULT_SKELETON_KEY_ACCEPTANCE_PATH)

    @patch("pyrit.executor.attack.single_turn.skeleton_key.SeedDataset.from_yaml_file")
    def test_init_only_loads_acceptance_file_when_prompt_provided(self, mock_dataset, mock_target):
        mock_seed = MagicMock()
        mock_seed.value = "Default acceptance"
        mock_dataset.return_value.prompts = [mock_seed]

        attack = SkeletonKeyAttack(objective_target=mock_target, skeleton_key_prompt="custom prompt")

        assert attack._skeleton_key_prompt == "custom prompt"
        assert attack._skeleton_key_acceptance == "Default acceptance"
        mock_dataset.assert_called_once_with(SkeletonKeyAttack.DEFAULT_SKELETON_KEY_ACCEPTANCE_PATH)

    @patch("pyrit.executor.attack.single_turn.skeleton_key.SeedDataset.from_yaml_file")
    def test_init_only_loads_prompt_file_when_acceptance_provided(self, mock_dataset, mock_target):
        mock_seed = MagicMock()
        mock_seed.value = "Default skeleton key"
        mock_dataset.return_value.prompts = [mock_seed]

        attack = SkeletonKeyAttack(objective_target=mock_target, skeleton_key_acceptance="custom acceptance")

        assert attack._skeleton_key_prompt == "Default skeleton key"
        assert attack._skeleton_key_acceptance == "custom acceptance"
        mock_dataset.assert_called_once_with(SkeletonKeyAttack.DEFAULT_SKELETON_KEY_PROMPT_PATH)

    def test_init_with_all_configurations(self, mock_target, mock_true_false_scorer, mock_prompt_normalizer):
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            attack_converter_config=AttackConverterConfig(),
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_true_false_scorer),
            prompt_normalizer=mock_prompt_normalizer,
            skeleton_key_prompt="Custom skeleton key",
            skeleton_key_acceptance="Custom acceptance",
            max_attempts_on_failure=3,
        )

        assert attack._objective_target == mock_target
        assert attack._skeleton_key_prompt == "Custom skeleton key"
        assert attack._skeleton_key_acceptance == "Custom acceptance"
        assert attack._prompt_normalizer == mock_prompt_normalizer
        assert attack._max_attempts_on_failure == 3
        assert attack._objective_scorer == mock_true_false_scorer

    def test_default_skeleton_key_prompt_path_exists(self):
        expected_suffix = Path("pyrit/datasets/executors/skeleton_key/skeleton_key.prompt")
        assert str(SkeletonKeyAttack.DEFAULT_SKELETON_KEY_PROMPT_PATH).endswith(str(expected_suffix))

    def test_default_skeleton_key_acceptance_path_exists(self):
        expected_suffix = Path("pyrit/datasets/executors/skeleton_key/skeleton_key_acceptance.prompt")
        assert str(SkeletonKeyAttack.DEFAULT_SKELETON_KEY_ACCEPTANCE_PATH).endswith(str(expected_suffix))

    def test_default_skeleton_key_prompt_file_loads(self, mock_target):
        """The default skeleton key prompt file should exist on disk and load with non-empty content."""
        attack = SkeletonKeyAttack(objective_target=mock_target)
        assert attack._skeleton_key_prompt.strip() != ""

    def test_default_skeleton_key_acceptance_file_loads(self, mock_target):
        """The default skeleton key acceptance file should exist on disk and load with non-empty content."""
        attack = SkeletonKeyAttack(objective_target=mock_target)
        assert attack._skeleton_key_acceptance.strip() != ""

    def test_skeleton_key_attack_inherits_parent_validation(self, mock_target):
        with pytest.raises(ValueError):
            SkeletonKeyAttack(objective_target=mock_target, max_attempts_on_failure=-1)


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeySetup:
    """Test _setup_async prepended conversation construction."""

    async def test_setup_creates_two_prepended_messages(self, mock_target, basic_context):
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk prompt",
            skeleton_key_acceptance="acceptance",
        )

        with patch(
            "pyrit.executor.attack.single_turn.prompt_sending.PromptSendingAttack._setup_async",
            new_callable=AsyncMock,
        ):
            await attack._setup_async(context=basic_context)

        assert basic_context.prepended_conversation is not None
        assert len(basic_context.prepended_conversation) == 2

    async def test_setup_prepended_messages_have_correct_roles(self, mock_target, basic_context):
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk prompt",
            skeleton_key_acceptance="acceptance",
        )

        with patch(
            "pyrit.executor.attack.single_turn.prompt_sending.PromptSendingAttack._setup_async",
            new_callable=AsyncMock,
        ):
            await attack._setup_async(context=basic_context)

        assert basic_context.prepended_conversation[0].api_role == "user"
        assert basic_context.prepended_conversation[1].api_role == "assistant"

    async def test_setup_prepended_messages_have_correct_content(self, mock_target, basic_context):
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="the skeleton key",
            skeleton_key_acceptance="the acceptance",
        )

        with patch(
            "pyrit.executor.attack.single_turn.prompt_sending.PromptSendingAttack._setup_async",
            new_callable=AsyncMock,
        ):
            await attack._setup_async(context=basic_context)

        assert basic_context.prepended_conversation[0].message_pieces[0].original_value == "the skeleton key"
        assert basic_context.prepended_conversation[1].message_pieces[0].original_value == "the acceptance"

    async def test_setup_delegates_to_parent_setup(self, mock_target, basic_context):
        """SkeletonKeyAttack must delegate to PromptSendingAttack._setup_async so that
        request_converters, prepended_conversation_config, and conversation_id are
        handled consistently with other single-turn attacks (role_play, context_compliance).
        """
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk",
            skeleton_key_acceptance="acc",
        )

        with patch(
            "pyrit.executor.attack.single_turn.prompt_sending.PromptSendingAttack._setup_async",
            new_callable=AsyncMock,
        ) as mock_parent_setup:
            await attack._setup_async(context=basic_context)

        mock_parent_setup.assert_called_once_with(context=basic_context)


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackStateManagement:
    """Test skeleton key attack state management across multiple setups."""

    async def test_separate_setups_produce_different_conversation_ids(self, mock_target):
        """Conversation IDs come from the parent _setup_async; verify they differ across runs."""
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk",
            skeleton_key_acceptance="acc",
        )

        context1 = SingleTurnAttackContext(
            params=AttackParameters(objective="Objective 1"),
            conversation_id=str(uuid.uuid4()),
        )
        context2 = SingleTurnAttackContext(
            params=AttackParameters(objective="Objective 2"),
            conversation_id=str(uuid.uuid4()),
        )

        with patch.object(attack._conversation_manager, "initialize_context_async", new_callable=AsyncMock):
            await attack._setup_async(context=context1)
            await attack._setup_async(context=context2)

        assert context1.conversation_id != context2.conversation_id

    async def test_separate_setups_produce_independent_prepended_conversations(self, mock_target):
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk",
            skeleton_key_acceptance="acc",
        )

        context1 = SingleTurnAttackContext(
            params=AttackParameters(objective="Objective 1"),
            conversation_id=str(uuid.uuid4()),
        )
        context2 = SingleTurnAttackContext(
            params=AttackParameters(objective="Objective 2"),
            conversation_id=str(uuid.uuid4()),
        )

        with patch(
            "pyrit.executor.attack.single_turn.prompt_sending.PromptSendingAttack._setup_async",
            new_callable=AsyncMock,
        ):
            await attack._setup_async(context=context1)
            await attack._setup_async(context=context2)

        assert context1.prepended_conversation is not context2.prepended_conversation


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackExecuteAsync:
    """Tests for end-to-end attack execution via execute_with_context_async."""

    async def test_attack_simple(self, mock_target, basic_context):
        """Verify execute_with_context_async invokes _validate_context, _setup_async, _perform_async."""
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk",
            skeleton_key_acceptance="acc",
        )
        attack._validate_context = MagicMock()
        attack._setup_async = AsyncMock()

        mock_result = AttackResult(
            conversation_id=basic_context.conversation_id,
            objective=basic_context.objective,
            outcome=AttackOutcome.SUCCESS,
        )
        attack._perform_async = AsyncMock(return_value=mock_result)

        result = await attack.execute_with_context_async(context=basic_context)

        assert result == mock_result
        attack._validate_context.assert_called_once_with(context=basic_context)
        attack._setup_async.assert_called_once_with(context=basic_context)
        attack._perform_async.assert_called_once_with(context=basic_context)

    async def test_attack_with_scorer_success(self, mock_target, basic_context, mock_true_false_scorer, success_score):
        """When the scorer reports success the result outcome should be SUCCESS."""
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk",
            skeleton_key_acceptance="acc",
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_true_false_scorer),
        )
        attack._validate_context = MagicMock()
        attack._setup_async = AsyncMock()

        mock_result = AttackResult(
            conversation_id=basic_context.conversation_id,
            objective=basic_context.objective,
            outcome=AttackOutcome.SUCCESS,
        )
        attack._perform_async = AsyncMock(return_value=mock_result)

        with patch(
            "pyrit.executor.attack.single_turn.prompt_sending.Scorer.score_response_async",
            new_callable=AsyncMock,
            return_value={"auxiliary_scores": [], "objective_scores": [success_score]},
        ):
            result = await attack.execute_with_context_async(context=basic_context)

        assert result.outcome == AttackOutcome.SUCCESS

    async def test_perform_async_executed_turns_is_single_turn(
        self, mock_target, basic_context, sample_response, mock_true_false_scorer, success_score
    ):
        """The refactored skeleton key is a single-turn attack; executed_turns should be 1."""
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk",
            skeleton_key_acceptance="acc",
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_true_false_scorer),
        )

        with (
            patch.object(attack, "_setup_async", new_callable=AsyncMock),
            patch.object(
                attack, "_send_prompt_to_objective_target_async", new_callable=AsyncMock, return_value=sample_response
            ),
            patch.object(attack, "_evaluate_response_async", new_callable=AsyncMock, return_value=success_score),
        ):
            result = await attack._perform_async(context=basic_context)

        assert result.executed_turns == 1

    async def test_perform_async_sends_objective_after_setup(
        self, mock_target, basic_context, sample_response, mock_true_false_scorer, success_score
    ):
        """The objective (not the skeleton key) must be the message sent to the target."""
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk prompt content",
            skeleton_key_acceptance="acc content",
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_true_false_scorer),
        )

        with (
            patch.object(attack, "_setup_async", new_callable=AsyncMock),
            patch.object(
                attack, "_send_prompt_to_objective_target_async", new_callable=AsyncMock, return_value=sample_response
            ) as mock_send,
            patch.object(attack, "_evaluate_response_async", new_callable=AsyncMock, return_value=success_score),
        ):
            await attack._perform_async(context=basic_context)

        sent_message = mock_send.call_args.kwargs["message"]
        sent_text = sent_message.message_pieces[0].original_value
        assert sent_text == basic_context.objective
        assert "sk prompt content" not in sent_text
        assert "acc content" not in sent_text


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackParamsType:
    """Tests for params_type in SkeletonKeyAttack."""

    def test_params_type_excludes_next_message(self, mock_target):
        attack = SkeletonKeyAttack(objective_target=mock_target)
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "next_message" not in fields

    def test_params_type_excludes_prepended_conversation(self, mock_target):
        attack = SkeletonKeyAttack(objective_target=mock_target)
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "prepended_conversation" not in fields

    def test_params_type_includes_objective(self, mock_target):
        attack = SkeletonKeyAttack(objective_target=mock_target)
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "objective" in fields
