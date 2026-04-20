# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from unittest.mock import MagicMock, patch

import pytest

from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_strategy import (
    AttackContext,
    AttackStrategy,
    _DefaultAttackStrategyEventHandler,
)
from pyrit.executor.core import StrategyEvent, StrategyEventData
from pyrit.identifiers import ComponentIdentifier
from pyrit.memory.central_memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
)
from pyrit.prompt_target import PromptTarget


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test",
    )


@pytest.fixture
def mock_memory():
    """Mock CentralMemory instance"""
    memory = MagicMock(spec=CentralMemory)
    memory.add_attack_results_to_memory = MagicMock()
    return memory


@pytest.fixture
def mock_objective_target():
    """Mock PromptTarget instance"""
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = _mock_target_id("MockTarget")
    return target


@pytest.fixture
def sample_attack_context():
    """Create a sample AttackContext for testing"""

    class TestAttackContext(AttackContext):
        pass

    params = AttackParameters(
        objective="Test harmful objective",
        memory_labels={"test": "label"},
    )
    return TestAttackContext(params=params)


@pytest.fixture
def sample_attack_result():
    """Create a sample AttackResult for testing"""
    return AttackResult(
        conversation_id="test-conversation-id",
        objective="Test objective",
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Test successful",
        execution_time_ms=0,
        executed_turns=1,
    )


@pytest.fixture
def mock_logger():
    """Create a mock logger for testing"""
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def event_handler(mock_logger):
    """Create an event handler for testing"""
    return _DefaultAttackStrategyEventHandler(logger=mock_logger)


@pytest.fixture
def mock_attack_strategy():
    """Create a mock attack strategy with all abstract methods mocked"""
    mock_target = MagicMock(spec=PromptTarget)

    class TestableAttackStrategy(AttackStrategy):
        def __init__(self, **kwargs):
            if "objective_target" not in kwargs:
                kwargs["objective_target"] = mock_target
            super().__init__(context_type=AttackContext, logger=kwargs.get("logger", logging.getLogger()), **kwargs)

        # Mock abstract methods from Strategy
        def _validate_context(self, *, context):
            pass

        async def _setup_async(self, *, context):
            pass

        async def _perform_async(self, *, context):
            return AttackResult(
                conversation_id="test-conversation-id",
                objective="Test objective",
                outcome=AttackOutcome.SUCCESS,
                outcome_reason="Test successful",
                execution_time_ms=0,
                executed_turns=1,
            )

        async def _teardown_async(self, *, context):
            pass

    return TestableAttackStrategy()


@pytest.mark.usefixtures("patch_central_database")
class TestAttackStrategyInitialization:
    """Tests for AttackStrategy initialization"""

    def test_init_creates_default_event_handler(self, mock_objective_target):
        """Test that AttackStrategy creates a default event handler"""

        class TestStrategy(AttackStrategy):
            def _validate_context(self, *, context):
                pass

            async def _setup_async(self, *, context):
                pass

            async def _perform_async(self, *, context):
                return AttackResult(
                    conversation_id="test-conversation-id",
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    outcome_reason="Test successful",
                    execution_time_ms=0,
                    executed_turns=1,
                )

            async def _teardown_async(self, *, context):
                pass

        strategy = TestStrategy(context_type=AttackContext, objective_target=mock_objective_target)

        assert len(strategy._event_handlers) == 1
        handler_name = "_DefaultAttackStrategyEventHandler"
        assert handler_name in strategy._event_handlers
        assert isinstance(strategy._event_handlers[handler_name], _DefaultAttackStrategyEventHandler)

    def test_init_with_custom_logger(self, mock_objective_target):
        """Test that AttackStrategy accepts a custom logger"""
        custom_logger = logging.getLogger("test_attack_logger")

        class TestStrategy(AttackStrategy):
            def _validate_context(self, *, context):
                pass

            async def _setup_async(self, *, context):
                pass

            async def _perform_async(self, *, context):
                return AttackResult(
                    conversation_id="test-conversation-id",
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    outcome_reason="Test successful",
                    execution_time_ms=0,
                    executed_turns=1,
                )

            async def _teardown_async(self, *, context):
                pass

        strategy = TestStrategy(
            context_type=AttackContext, objective_target=mock_objective_target, logger=custom_logger
        )

        assert strategy._logger.logger == custom_logger

    def test_init_sets_memory_labels_from_default_values(self, mock_objective_target):
        """Test that memory labels are loaded from default values"""
        with patch("pyrit.executor.core.strategy.default_values") as mock_default:
            mock_default.get_non_required_value.return_value = '{"env_label": "env_value"}'

            class TestStrategy(AttackStrategy):
                def _validate_context(self, *, context):
                    pass

                async def _setup_async(self, *, context):
                    pass

                async def _perform_async(self, *, context):
                    return AttackResult(
                        conversation_id="test-conversation-id",
                        objective="Test objective",
                        outcome=AttackOutcome.SUCCESS,
                        outcome_reason="Test successful",
                        execution_time_ms=0,
                        executed_turns=1,
                    )

                async def _teardown_async(self, *, context):
                    pass

            strategy = TestStrategy(context_type=AttackContext, objective_target=mock_objective_target)

            assert strategy._memory_labels == {"env_label": "env_value"}


@pytest.mark.usefixtures("patch_central_database")
class TestAttackStrategyExecution:
    """Tests for AttackStrategy execution methods"""

    @pytest.mark.asyncio
    async def test_execute_async_with_objective_creates_context(self, mock_attack_strategy):
        """Test that execute_async with objective parameter creates context and executes."""
        objective = "Test objective"
        memory_labels = {"test": "value"}

        # Call execute_async - it should create context internally and execute
        result = await mock_attack_strategy.execute_async(
            objective=objective,
            memory_labels=memory_labels,
        )

        # Verify we got a result
        assert result is not None
        assert isinstance(result, AttackResult)

    @pytest.mark.asyncio
    async def test_execute_async_with_prepended_conversation(self, mock_attack_strategy):
        """Test that execute_async handles prepended_conversation parameter."""
        objective = "Test objective"
        prepended_conversation = [Message.from_prompt(prompt="Test", role="user")]

        # This should work without errors
        result = await mock_attack_strategy.execute_async(
            objective=objective,
            prepended_conversation=prepended_conversation,
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_execute_async_requires_objective(self, mock_attack_strategy):
        """Test that execute_async requires objective parameter."""
        with pytest.raises(ValueError, match="objective is required"):
            await mock_attack_strategy.execute_async()

    @pytest.mark.asyncio
    async def test_execute_async_rejects_unknown_params(self, mock_attack_strategy):
        """Test that execute_async rejects unknown parameters."""
        with pytest.raises(ValueError, match="does not accept parameters"):
            await mock_attack_strategy.execute_async(
                objective="Test",
                unknown_param="value",
            )

    @pytest.mark.asyncio
    async def test_execute_async_allows_optional_parameters_as_none(self, mock_attack_strategy):
        """Test that execute_async works with optional parameters as None."""
        # None values should be skipped, not cause errors
        result = await mock_attack_strategy.execute_async(
            objective="Test objective",
            memory_labels=None,
            prepended_conversation=None,
        )

        assert result is not None


@pytest.mark.usefixtures("patch_central_database")
class TestDefaultAttackStrategyEventHandler:
    """Tests for the default attack strategy event handler"""

    @pytest.mark.asyncio
    async def test_on_pre_execute_sets_start_time(self, event_handler, sample_attack_context, mock_logger):
        """Test that pre-execute handler sets start time"""
        event_data = StrategyEventData(
            event=StrategyEvent.ON_PRE_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
        )

        with patch("time.perf_counter", return_value=123.456):
            await event_handler.on_event(event_data)

        assert sample_attack_context.start_time == 123.456

    @pytest.mark.asyncio
    async def test_on_pre_execute_logs_objective(self, event_handler, sample_attack_context, mock_logger):
        """Test that pre-execute handler logs the objective"""
        event_data = StrategyEventData(
            event=StrategyEvent.ON_PRE_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
        )

        await event_handler.on_event(event_data)
        mock_logger.info.assert_called_once_with(f"Starting attack: {sample_attack_context.objective}")

    @pytest.mark.asyncio
    async def test_on_pre_execute_raises_on_none_context(self, event_handler, mock_logger):
        """Test that pre-execute handler raises error for None context"""
        # Create a dummy context that we'll set to None in the event data
        dummy_context = MagicMock()

        event_data = StrategyEventData(
            event=StrategyEvent.ON_PRE_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=dummy_context,  # Will be checked inside the handler
        )
        # Set context to None after creation to test the validation
        event_data.context = None

        with pytest.raises(ValueError, match="Attack context is None"):
            await event_handler.on_event(event_data)

    @pytest.mark.asyncio
    async def test_on_post_execute_calculates_execution_time(
        self, event_handler, sample_attack_context, sample_attack_result, mock_logger
    ):
        """Test that post-execute handler calculates execution time"""
        sample_attack_context.start_time = 100.0

        event_data = StrategyEventData(
            event=StrategyEvent.ON_POST_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
            result=sample_attack_result,
        )

        with patch("time.perf_counter", return_value=100.5):  # 500ms later
            await event_handler.on_event(event_data)

        assert sample_attack_result.execution_time_ms == 500

    @pytest.mark.asyncio
    async def test_on_post_execute_logs_success(
        self, event_handler, sample_attack_context, sample_attack_result, mock_logger
    ):
        """Test that post-execute handler logs successful outcome"""
        sample_attack_result.outcome = AttackOutcome.SUCCESS
        sample_attack_result.outcome_reason = "Test successful"

        event_data = StrategyEventData(
            event=StrategyEvent.ON_POST_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
            result=sample_attack_result,
        )

        await event_handler.on_event(event_data)

        expected_message = f"{event_handler.__class__.__name__} achieved the objective. Reason: Test successful"
        mock_logger.info.assert_called_with(expected_message)

    @pytest.mark.asyncio
    async def test_on_post_execute_logs_failure(
        self, event_handler, sample_attack_context, sample_attack_result, mock_logger
    ):
        """Test that post-execute handler logs failed outcome"""
        sample_attack_result.outcome = AttackOutcome.FAILURE
        sample_attack_result.outcome_reason = "Test failed"

        event_data = StrategyEventData(
            event=StrategyEvent.ON_POST_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
            result=sample_attack_result,
        )

        await event_handler.on_event(event_data)

        expected_message = f"{event_handler.__class__.__name__} did not achieve the objective. Reason: Test failed"
        mock_logger.info.assert_called_with(expected_message)

    @pytest.mark.asyncio
    async def test_on_post_execute_logs_undetermined(
        self, event_handler, sample_attack_context, sample_attack_result, mock_logger
    ):
        """Test that post-execute handler logs undetermined outcome"""
        sample_attack_result.outcome = AttackOutcome.UNDETERMINED
        sample_attack_result.outcome_reason = None

        event_data = StrategyEventData(
            event=StrategyEvent.ON_POST_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
            result=sample_attack_result,
        )

        await event_handler.on_event(event_data)

        expected_message = f"{event_handler.__class__.__name__} outcome is undetermined. Reason: Not specified"
        mock_logger.info.assert_called_with(expected_message)

    @pytest.mark.asyncio
    async def test_on_post_execute_adds_results_to_memory(self, mock_memory):
        """Test that post-execute handler adds results to memory"""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()

            sample_context = MagicMock()
            sample_context.start_time = 100.0
            sample_result = MagicMock(spec=AttackResult)

            event_data = StrategyEventData(
                event=StrategyEvent.ON_POST_EXECUTE,
                strategy_name="TestStrategy",
                strategy_id="test-id",
                context=sample_context,
                result=sample_result,
            )

            with patch("time.perf_counter", return_value=100.1):
                await handler.on_event(event_data)

            mock_memory.add_attack_results_to_memory.assert_called_once_with(attack_results=[sample_result])

    @pytest.mark.asyncio
    async def test_on_post_execute_raises_on_none_result(self, event_handler, sample_attack_context, mock_logger):
        """Test that post-execute handler raises error for None result"""
        # Create a dummy result that we'll set to None
        dummy_result = MagicMock(spec=AttackResult)

        event_data = StrategyEventData(
            event=StrategyEvent.ON_POST_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
            result=dummy_result,
        )
        # Set result to None after creation to test the validation
        event_data.result = None

        with pytest.raises(ValueError, match="Attack result is None"):
            await event_handler.on_event(event_data)

    @pytest.mark.asyncio
    async def test_on_event_handles_other_events(self, event_handler, sample_attack_context, mock_logger):
        """Test that on_event handles events not in the specific handlers"""
        event_data = StrategyEventData(
            event=StrategyEvent.ON_PRE_VALIDATE,  # Not specifically handled
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
        )

        await event_handler.on_event(event_data)

        # Should call the generic _on method and log debug message
        mock_logger.debug.assert_called_once_with(
            f"Attack is in '{StrategyEvent.ON_PRE_VALIDATE.value}' stage for {event_handler.__class__.__name__}"
        )


@pytest.mark.usefixtures("patch_central_database")
class TestAttackStrategyIntegration:
    """Integration tests for AttackStrategy with event handlers"""

    @pytest.mark.asyncio
    async def test_attack_strategy_event_flow(self, mock_memory, mock_objective_target):
        """Test that AttackStrategy properly triggers events during execution"""

        class TestStrategy(AttackStrategy):
            def _validate_context(self, *, context):
                pass

            async def _setup_async(self, *, context):
                pass

            async def _perform_async(self, *, context):
                return AttackResult(
                    conversation_id="test-conversation-id",
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    outcome_reason="Test successful",
                    executed_turns=1,
                )

            async def _teardown_async(self, *, context):
                pass

        strategy = TestStrategy(context_type=AttackContext, objective_target=mock_objective_target)

        with patch("time.perf_counter", side_effect=[100.0, 100.5]):  # Start and end times
            result = await strategy.execute_async(objective="Test objective")

        # Verify the strategy executes and returns a result
        assert result is not None
        assert result.conversation_id == "test-conversation-id"
        assert result.objective == "Test objective"
        assert result.outcome == AttackOutcome.SUCCESS

        # Current behavior: execution_time_ms is not modified by event handler
        assert result.execution_time_ms == 500

    @pytest.mark.asyncio
    async def test_attack_strategy_with_custom_event_handler(self, mock_objective_target):
        """Test that AttackStrategy can work with custom event handlers"""
        custom_handler_called = False

        class CustomEventHandler:
            async def on_event(self, event_data):
                nonlocal custom_handler_called
                custom_handler_called = True

        class TestStrategy(AttackStrategy):
            def _validate_context(self, *, context):
                pass

            async def _setup_async(self, *, context):
                pass

            async def _perform_async(self, *, context):
                return AttackResult(
                    conversation_id="test-conversation-id",
                    objective="Test objective",
                    outcome=AttackOutcome.SUCCESS,
                    outcome_reason="Test successful",
                    execution_time_ms=0,
                    executed_turns=1,
                )

            async def _teardown_async(self, *, context):
                pass

        # Note: The current AttackStrategy implementation doesn't expose a way to add custom handlers
        # This test documents the expected behavior if that capability is added
        strategy = TestStrategy(context_type=AttackContext, objective_target=mock_objective_target)

        # The default handler should still be present
        assert len(strategy._event_handlers) == 1
        assert "_DefaultAttackStrategyEventHandler" in strategy._event_handlers
