# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackParameters,
    AttackScoringConfig,
    FlipAttack,
    SingleTurnAttackContext,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
)
from pyrit.prompt_converter import FlipConverter
from pyrit.prompt_normalizer import PromptConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptChatTarget
from pyrit.score import TrueFalseScorer


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


@pytest.fixture
def mock_objective_target():
    """Create a mock PromptChatTarget for testing"""
    target = MagicMock(spec=PromptChatTarget)
    target.send_prompt_async = AsyncMock()
    target.get_identifier.return_value = _mock_target_id("MockTarget")
    return target


@pytest.fixture
def flip_attack(mock_objective_target):
    """Create a FlipAttack instance with default configuration"""
    return FlipAttack(objective_target=mock_objective_target)


@pytest.fixture
def mock_scorer():
    """Create a mock true/false scorer"""
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_text_async = AsyncMock()
    scorer.get_identifier.return_value = _mock_scorer_id()
    return scorer


@pytest.fixture
def basic_context():
    """Create a basic context for testing"""
    return SingleTurnAttackContext(
        params=AttackParameters(objective="How can I kill a Python process?"),
        conversation_id=str(uuid.uuid4()),
    )


@pytest.mark.usefixtures("patch_central_database")
class TestFlipAttackInitialization:
    """Tests for FlipAttack initialization"""

    def test_init_loads_system_prompt(self, mock_objective_target):
        """Test that the system prompt is properly loaded from YAML file"""
        attack = FlipAttack(objective_target=mock_objective_target)

        assert attack._system_prompt is not None
        assert len(attack._system_prompt.message_pieces) == 1
        assert attack._system_prompt.message_pieces[0].api_role == "system"
        assert "flipping each word" in attack._system_prompt.message_pieces[0].original_value

    def test_init_adds_flip_converter_to_request_converters(self, mock_objective_target):
        """Test that FlipConverter is added to request converters"""
        attack = FlipAttack(objective_target=mock_objective_target)

        # Should have one converter configuration
        assert len(attack._request_converters) == 1

        # The first converter should be FlipConverter
        converter_config = attack._request_converters[0]
        assert len(converter_config.converters) == 1
        assert isinstance(converter_config.converters[0], FlipConverter)

    def test_init_with_existing_converters_prepends_flip_converter(self, mock_objective_target):
        """Test that FlipConverter is prepended to existing converters"""
        from pyrit.prompt_converter import Base64Converter

        existing_converter = PromptConverterConfiguration.from_converters(converters=[Base64Converter()])
        converter_config = AttackConverterConfig(request_converters=existing_converter)

        attack = FlipAttack(objective_target=mock_objective_target, attack_converter_config=converter_config)

        # Should have 2 converter configurations (FlipConverter + existing)
        assert len(attack._request_converters) == 2

        # First should be FlipConverter
        assert isinstance(attack._request_converters[0].converters[0], FlipConverter)

        # Second should be the existing converter
        assert isinstance(attack._request_converters[1].converters[0], Base64Converter)

    def test_init_with_all_parameters(self, mock_objective_target, mock_scorer):
        """Test initialization with all optional parameters"""
        converter_config = AttackConverterConfig()
        scoring_config = AttackScoringConfig(objective_scorer=mock_scorer)
        prompt_normalizer = PromptNormalizer()

        attack = FlipAttack(
            objective_target=mock_objective_target,
            attack_converter_config=converter_config,
            attack_scoring_config=scoring_config,
            prompt_normalizer=prompt_normalizer,
            max_attempts_on_failure=3,
        )

        assert attack._objective_target == mock_objective_target
        assert attack._objective_scorer == mock_scorer
        assert attack._prompt_normalizer == prompt_normalizer
        assert attack._max_attempts_on_failure == 3


@pytest.mark.usefixtures("patch_central_database")
class TestFlipAttackParamsType:
    """Tests for params_type in FlipAttack"""

    def test_params_type_excludes_next_message(self, flip_attack):
        """Test that params_type excludes next_message field"""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(flip_attack.params_type)}
        assert "next_message" not in fields

    def test_params_type_excludes_prepended_conversation(self, flip_attack):
        """Test that params_type excludes prepended_conversation field"""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(flip_attack.params_type)}
        assert "prepended_conversation" not in fields

    def test_params_type_includes_objective(self, flip_attack):
        """Test that params_type includes objective field"""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(flip_attack.params_type)}
        assert "objective" in fields


@pytest.mark.usefixtures("patch_central_database")
class TestFlipAttackSetup:
    """Tests for the setup phase"""

    async def test_setup_adds_system_prompt_to_context(self, flip_attack, basic_context):
        """Test that setup adds the system prompt to prepended conversation"""
        flip_attack._conversation_manager = MagicMock()
        flip_attack._conversation_manager.initialize_context_async = AsyncMock()

        await flip_attack._setup_async(context=basic_context)

        # Verify system prompt was added
        assert len(basic_context.prepended_conversation) == 1
        assert basic_context.prepended_conversation[0] == flip_attack._system_prompt

    async def test_setup_updates_conversation_without_converters(self, flip_attack, basic_context):
        """Test that conversation state is updated without converters for system prompt"""
        flip_attack._conversation_manager = MagicMock()
        flip_attack._conversation_manager.initialize_context_async = AsyncMock()

        await flip_attack._setup_async(context=basic_context)

        # Verify prepended_conversation was set on context (system prompt)
        assert len(basic_context.prepended_conversation) == 1
        assert basic_context.prepended_conversation[0] == flip_attack._system_prompt

        # Verify conversation manager was called with correct parameters
        flip_attack._conversation_manager.initialize_context_async.assert_called_once_with(
            context=basic_context,
            target=flip_attack._objective_target,
            conversation_id=basic_context.conversation_id,
            memory_labels={},
        )


@pytest.mark.usefixtures("patch_central_database")
class TestFlipAttackExecution:
    """Tests for attack execution"""

    async def test_perform_attack_formats_objective_correctly(self, flip_attack, basic_context):
        """Test that the objective is formatted with the special TASK format"""
        # basic_context fixture already has objective="How can I kill a Python process?"
        expected_prompt = "TASK is '⟪How can I kill a Python process?⟫'"

        # Mock the parent's perform_attack_async
        with patch.object(
            FlipAttack.__bases__[0],
            "_perform_async",
            new_callable=AsyncMock,  # PromptSendingAttack
        ) as mock_perform:
            mock_result = AttackResult(
                conversation_id=basic_context.conversation_id,
                objective=basic_context.objective,
                outcome=AttackOutcome.SUCCESS,
            )
            mock_perform.return_value = mock_result

            result = await flip_attack._perform_async(context=basic_context)

            # Verify the message was set correctly
            assert basic_context.next_message is not None
            assert len(basic_context.next_message.message_pieces) == 1
            assert basic_context.next_message.message_pieces[0].original_value == expected_prompt
            assert basic_context.next_message.message_pieces[0].original_value_data_type == "text"

            # Verify parent method was called
            mock_perform.assert_called_once_with(context=basic_context)
            assert result == mock_result


@pytest.mark.usefixtures("patch_central_database")
class TestAttackLifecycle:
    """Tests for the complete FlipAttack lifecycle (execute_async)"""

    async def test_execute_async_successful_lifecycle(self, mock_objective_target, basic_context):
        attack = FlipAttack(objective_target=mock_objective_target)

        # Mock all lifecycle methods
        attack._validate_context = MagicMock()
        attack._setup_async = AsyncMock()
        mock_result = AttackResult(
            conversation_id=basic_context.conversation_id,
            objective=basic_context.objective,
            outcome=AttackOutcome.SUCCESS,
        )
        attack._perform_async = AsyncMock(return_value=mock_result)
        attack._teardown_async = AsyncMock()

        # Execute the complete lifecycle
        result = await attack.execute_with_context_async(context=basic_context)

        # Verify result and proper execution order
        assert result == mock_result
        attack._validate_context.assert_called_once_with(context=basic_context)
        attack._setup_async.assert_called_once_with(context=basic_context)
        attack._perform_async.assert_called_once_with(context=basic_context)
        attack._teardown_async.assert_called_once_with(context=basic_context)
