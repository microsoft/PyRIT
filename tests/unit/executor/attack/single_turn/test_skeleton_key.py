# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

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

    def test_skeleton_key_attack_inherits_parent_validation(self, mock_target):
        with pytest.raises(ValueError):
            SkeletonKeyAttack(objective_target=mock_target, max_attempts_on_failure=-1)


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeySetup:
    """Test _setup_async prepended conversation construction."""

    async def test_setup_assigns_new_conversation_id(self, mock_target, basic_context):
        original_id = basic_context.conversation_id
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk prompt",
            skeleton_key_acceptance="acceptance",
        )

        with patch.object(attack._conversation_manager, "initialize_context_async", new_callable=AsyncMock):
            await attack._setup_async(context=basic_context)

        assert basic_context.conversation_id != original_id

    async def test_setup_creates_two_prepended_messages(self, mock_target, basic_context):
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk prompt",
            skeleton_key_acceptance="acceptance",
        )

        with patch.object(attack._conversation_manager, "initialize_context_async", new_callable=AsyncMock):
            await attack._setup_async(context=basic_context)

        assert basic_context.prepended_conversation is not None
        assert len(basic_context.prepended_conversation) == 2

    async def test_setup_prepended_messages_have_correct_roles(self, mock_target, basic_context):
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk prompt",
            skeleton_key_acceptance="acceptance",
        )

        with patch.object(attack._conversation_manager, "initialize_context_async", new_callable=AsyncMock):
            await attack._setup_async(context=basic_context)

        assert basic_context.prepended_conversation[0].api_role == "user"
        assert basic_context.prepended_conversation[1].api_role == "assistant"

    async def test_setup_prepended_messages_have_correct_content(self, mock_target, basic_context):
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="the skeleton key",
            skeleton_key_acceptance="the acceptance",
        )

        with patch.object(attack._conversation_manager, "initialize_context_async", new_callable=AsyncMock):
            await attack._setup_async(context=basic_context)

        assert basic_context.prepended_conversation[0].message_pieces[0].original_value == "the skeleton key"
        assert basic_context.prepended_conversation[1].message_pieces[0].original_value == "the acceptance"

    async def test_setup_calls_conversation_manager(self, mock_target, basic_context):
        attack = SkeletonKeyAttack(
            objective_target=mock_target,
            skeleton_key_prompt="sk",
            skeleton_key_acceptance="acc",
        )

        mock_init = AsyncMock()
        with patch.object(attack._conversation_manager, "initialize_context_async", mock_init):
            await attack._setup_async(context=basic_context)

        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs["context"] is basic_context
        assert call_kwargs["target"] is mock_target
        assert call_kwargs["conversation_id"] == basic_context.conversation_id


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackStateMangement:
    """Test skeleton key attack state management."""

    async def test_separate_setups_produce_different_conversation_ids(self, mock_target):
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

        with patch.object(attack._conversation_manager, "initialize_context_async", new_callable=AsyncMock):
            await attack._setup_async(context=context1)
            await attack._setup_async(context=context2)

        assert context1.prepended_conversation is not context2.prepended_conversation


@pytest.mark.usefixtures("patch_central_database")
class TestSkeletonKeyAttackParamsType:
    """Tests for params_type in SkeletonKeyAttack."""

    def test_params_type_excludes_next_message(self, mock_target):
        import dataclasses

        attack = SkeletonKeyAttack(objective_target=mock_target)
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "next_message" not in fields

    def test_params_type_excludes_prepended_conversation(self, mock_target):
        import dataclasses

        attack = SkeletonKeyAttack(objective_target=mock_target)
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "prepended_conversation" not in fields

    def test_params_type_includes_objective(self, mock_target):
        import dataclasses

        attack = SkeletonKeyAttack(objective_target=mock_target)
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert "objective" in fields
