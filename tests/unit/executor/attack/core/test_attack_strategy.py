# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from pyrit.exceptions.retry_collector import RetryCollector
from pyrit.executor.attack.core.attack_config import AttackAdversarialConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_strategy import (
    AttackContext,
    AttackStrategy,
    _DefaultAttackStrategyEventHandler,
)
from pyrit.executor.core import StrategyEvent, StrategyEventData
from pyrit.memory.central_memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Message,
    SeedPrompt,
)
from pyrit.models.identifiers import (
    AtomicAttackEvaluationIdentifier,
    AtomicAttackIdentifier,
)
from pyrit.models.retry_event import RetryEvent
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


def test_next_message_override_can_clear_parameter_value_and_survive_copy():
    """An explicit None override must not fall back to the immutable parameter after copying."""

    class TestAttackContext(AttackContext):
        pass

    seed_message = Message.from_prompt(prompt="seed", role="user")
    context = TestAttackContext(
        params=AttackParameters(
            objective="Test objective",
            next_message=seed_message,
        )
    )

    assert context.next_message is seed_message

    context.next_message = None

    assert context.next_message is None
    assert context.duplicate().next_message is None
    assert replace(context).next_message is None


def test_next_message_override_constructor_value_takes_precedence():
    """A directly supplied override must take precedence over the immutable parameter."""

    class TestAttackContext(AttackContext):
        pass

    seed_message = Message.from_prompt(prompt="seed", role="user")
    context = TestAttackContext(
        params=AttackParameters(
            objective="Test objective",
            next_message=seed_message,
        ),
        _next_message_override=None,
    )

    assert context.next_message is None


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

    async def test_execute_async_requires_objective(self, mock_attack_strategy):
        """Test that execute_async requires objective parameter."""
        with pytest.raises(ValueError, match="objective is required"):
            await mock_attack_strategy.execute_async()

    async def test_execute_async_rejects_unknown_params(self, mock_attack_strategy):
        """Test that execute_async rejects unknown parameters."""
        with pytest.raises(ValueError, match="does not accept parameters"):
            await mock_attack_strategy.execute_async(
                objective="Test",
                unknown_param="value",
            )

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

    async def test_on_pre_execute_sets_start_time(self, event_handler, sample_attack_context, mock_logger):
        """Test that pre-execute handler sets start time"""
        event_data = StrategyEventData(
            event=StrategyEvent.ON_PRE_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
        )

        with patch("time.perf_counter", return_value=123.456):
            await event_handler.on_event_async(event_data)

        assert sample_attack_context.start_time == 123.456

    async def test_on_pre_execute_logs_objective(self, event_handler, sample_attack_context, mock_logger):
        """Test that pre-execute handler logs the objective"""
        event_data = StrategyEventData(
            event=StrategyEvent.ON_PRE_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
        )

        await event_handler.on_event_async(event_data)
        mock_logger.info.assert_called_once_with(f"Starting attack: {sample_attack_context.objective}")

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
            await event_handler.on_event_async(event_data)

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
            await event_handler.on_event_async(event_data)

        assert sample_attack_result.execution_time_ms == 500

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

        await event_handler.on_event_async(event_data)

        expected_message = f"{event_handler.__class__.__name__} achieved the objective. Reason: Test successful"
        mock_logger.info.assert_called_with(expected_message)

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

        await event_handler.on_event_async(event_data)

        expected_message = f"{event_handler.__class__.__name__} did not achieve the objective. Reason: Test failed"
        mock_logger.info.assert_called_with(expected_message)

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

        await event_handler.on_event_async(event_data)

        expected_message = f"{event_handler.__class__.__name__} outcome is undetermined. Reason: Not specified"
        mock_logger.info.assert_called_with(expected_message)

    async def test_on_post_execute_logs_error_outcome(
        self, event_handler, sample_attack_context, sample_attack_result, mock_logger
    ):
        """Test that post-execute handler logs error outcome"""
        sample_attack_result.outcome = AttackOutcome.ERROR
        sample_attack_result.outcome_reason = "Connection timeout"

        event_data = StrategyEventData(
            event=StrategyEvent.ON_POST_EXECUTE,
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
            result=sample_attack_result,
        )

        await event_handler.on_event_async(event_data)

        expected_message = f"{event_handler.__class__.__name__} failed with an error. Reason: Connection timeout"
        mock_logger.info.assert_called_with(expected_message)

    async def test_on_post_execute_adds_results_to_memory(self, mock_memory):
        """Test that post-execute handler adds results to memory"""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()

            sample_context = MagicMock()
            sample_context.start_time = 100.0
            sample_result = AttackResult(
                conversation_id="conv-id",
                objective="test objective",
                outcome=AttackOutcome.SUCCESS,
            )

            event_data = StrategyEventData(
                event=StrategyEvent.ON_POST_EXECUTE,
                strategy_name="TestStrategy",
                strategy_id="test-id",
                context=sample_context,
                result=sample_result,
            )

            with patch("time.perf_counter", return_value=100.1):
                await handler.on_event_async(event_data)

            mock_memory.add_attack_results_to_memory.assert_called_once_with(attack_results=[sample_result])

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
            await event_handler.on_event_async(event_data)

    async def test_on_post_execute_attaches_retry_events(
        self, sample_attack_context, sample_attack_result, mock_memory
    ):
        """Test that post-execute handler attaches retry events from collector to the result"""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()

            sample_attack_context.start_time = 100.0
            retry_event = RetryEvent(
                attempt_number=1, function_name="send_prompt_async", exception_type="RateLimitError"
            )

            collector = RetryCollector(events=[retry_event])
            with patch("pyrit.executor.attack.core.attack_strategy.get_retry_collector", return_value=collector):
                event_data = StrategyEventData(
                    event=StrategyEvent.ON_POST_EXECUTE,
                    strategy_name="TestStrategy",
                    strategy_id="test-id",
                    context=sample_attack_context,
                    result=sample_attack_result,
                )
                await handler.on_event_async(event_data)

            assert sample_attack_result.retry_events == [retry_event]
            assert sample_attack_result.total_retries == 1

    async def test_on_post_execute_no_retry_events_when_collector_empty(
        self, sample_attack_context, sample_attack_result, mock_memory
    ):
        """Test that post-execute handler does not set retry_events when collector has no events"""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()

            sample_attack_context.start_time = 100.0
            collector = RetryCollector(events=[])
            with patch("pyrit.executor.attack.core.attack_strategy.get_retry_collector", return_value=collector):
                event_data = StrategyEventData(
                    event=StrategyEvent.ON_POST_EXECUTE,
                    strategy_name="TestStrategy",
                    strategy_id="test-id",
                    context=sample_attack_context,
                    result=sample_attack_result,
                )
                await handler.on_event_async(event_data)

            # Empty collector means the guard `if collector and collector.events` is False
            assert not sample_attack_result.retry_events
            assert sample_attack_result.total_retries == 0

    async def test_on_error_attaches_retry_events(self, sample_attack_context, mock_memory):
        """Test that error handler attaches collected retry events to the error AttackResult"""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()

            sample_attack_context.start_time = 100.0
            retry_event = RetryEvent(attempt_number=2, function_name="send_prompt_async", exception_type="TimeoutError")
            collector = RetryCollector(events=[retry_event])

            with patch("pyrit.executor.attack.core.attack_strategy.get_retry_collector", return_value=collector):
                event_data = StrategyEventData(
                    event=StrategyEvent.ON_ERROR,
                    strategy_name="TestStrategy",
                    strategy_id="test-id",
                    context=sample_attack_context,
                    error=RuntimeError("test error"),
                )
                await handler.on_event_async(event_data)

            stored_result = mock_memory.add_attack_results_to_memory.call_args.kwargs["attack_results"][0]
            assert stored_result.outcome == AttackOutcome.ERROR
            assert stored_result.retry_events == [retry_event]
            assert stored_result.total_retries == 1

    async def test_on_error_empty_retry_events_when_no_collector(self, sample_attack_context, mock_memory):
        """Test that error handler sets empty retry_events when no collector exists"""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()

            sample_attack_context.start_time = 100.0

            with patch("pyrit.executor.attack.core.attack_strategy.get_retry_collector", return_value=None):
                event_data = StrategyEventData(
                    event=StrategyEvent.ON_ERROR,
                    strategy_name="TestStrategy",
                    strategy_id="test-id",
                    context=sample_attack_context,
                    error=RuntimeError("test error"),
                )
                await handler.on_event_async(event_data)

            stored_result = mock_memory.add_attack_results_to_memory.call_args.kwargs["attack_results"][0]
            assert stored_result.retry_events == []
            assert stored_result.total_retries == 0

    async def test_on_error_persists_result_to_memory(self, sample_attack_context, mock_memory):
        """Test that error handler creates an error AttackResult and persists it"""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()

            sample_attack_context.start_time = 100.0
            error = ValueError("something broke")

            with patch("pyrit.executor.attack.core.attack_strategy.get_retry_collector", return_value=None):
                event_data = StrategyEventData(
                    event=StrategyEvent.ON_ERROR,
                    strategy_name="TestStrategy",
                    strategy_id="test-id",
                    context=sample_attack_context,
                    error=error,
                )
                with patch("time.perf_counter", return_value=100.5):
                    await handler.on_event_async(event_data)

            mock_memory.add_attack_results_to_memory.assert_called_once()
            stored_result = mock_memory.add_attack_results_to_memory.call_args.kwargs["attack_results"][0]
            assert stored_result.outcome == AttackOutcome.ERROR
            assert stored_result.error_message == "something broke"
            assert stored_result.error_type == "ValueError"
            assert stored_result.execution_time_ms == 500

    async def test_on_error_skips_when_no_error_or_context(self, mock_memory):
        """Test that error handler returns early when error or context is None"""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()

            event_data = StrategyEventData(
                event=StrategyEvent.ON_ERROR,
                strategy_name="TestStrategy",
                strategy_id="test-id",
                context=None,
                error=RuntimeError("test"),
            )
            await handler.on_event_async(event_data)
            mock_memory.add_attack_results_to_memory.assert_not_called()

    async def test_on_event_handles_other_events(self, event_handler, sample_attack_context, mock_logger):
        """Test that on_event_async handles events not in the specific handlers"""
        event_data = StrategyEventData(
            event=StrategyEvent.ON_PRE_VALIDATE,  # Not specifically handled
            strategy_name="TestStrategy",
            strategy_id="test-id",
            context=sample_attack_context,
        )

        await event_handler.on_event_async(event_data)

        # Should call the generic _on_async method and log debug message
        mock_logger.debug.assert_called_once_with(
            f"Attack is in '{StrategyEvent.ON_PRE_VALIDATE.value}' stage for {event_handler.__class__.__name__}"
        )

    async def test_on_post_execute_stamps_scenario_attribution_when_present(
        self, sample_attack_context, sample_attack_result, mock_memory
    ):
        """When the context carries an AttackResultAttribution, the persisted
        AttackResult must have attribution_parent_id + attribution_data populated."""
        from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution

        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()
            sample_attack_context.start_time = 100.0
            sample_attack_context._attribution = AttackResultAttribution(
                parent_id="scenario-1",
                parent_collection="atomic_a",
            )

            event_data = StrategyEventData(
                event=StrategyEvent.ON_POST_EXECUTE,
                strategy_name="TestStrategy",
                strategy_id="test-id",
                context=sample_attack_context,
                result=sample_attack_result,
            )
            await handler.on_event_async(event_data)

        assert sample_attack_result.attribution_parent_id == "scenario-1"
        assert sample_attack_result.attribution_data == {
            "parent_collection": "atomic_a",
        }

    async def test_on_post_execute_no_attribution_leaves_fields_none(
        self, sample_attack_context, sample_attack_result, mock_memory
    ):
        """Outside a Scenario, _attribution is None and the attribution fields
        on the persisted AttackResult must stay None."""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()
            sample_attack_context.start_time = 100.0
            # _attribution defaults to None — no scenario stamping should happen.

            event_data = StrategyEventData(
                event=StrategyEvent.ON_POST_EXECUTE,
                strategy_name="TestStrategy",
                strategy_id="test-id",
                context=sample_attack_context,
                result=sample_attack_result,
            )
            await handler.on_event_async(event_data)

        assert sample_attack_result.attribution_parent_id is None
        assert sample_attack_result.attribution_data is None

    async def test_on_error_stamps_scenario_attribution_when_present(self, sample_attack_context, mock_memory):
        """Error AttackResults must also carry the attribution foreign key so
        error lookups via get_attack_results(scenario_result_id=..., outcome=ERROR) work."""
        from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution

        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()
            sample_attack_context.start_time = 100.0
            sample_attack_context._attribution = AttackResultAttribution(
                parent_id="scenario-err",
                parent_collection="atomic_err",
            )

            event_data = StrategyEventData(
                event=StrategyEvent.ON_ERROR,
                strategy_name="TestStrategy",
                strategy_id="test-id",
                context=sample_attack_context,
                error=RuntimeError("boom"),
            )
            await handler.on_event_async(event_data)

        # The error AttackResult was persisted; inspect what was sent to memory.
        call = mock_memory.add_attack_results_to_memory.call_args
        persisted = call.kwargs["attack_results"][0]
        assert persisted.outcome == AttackOutcome.ERROR
        assert persisted.attribution_parent_id == "scenario-err"
        assert persisted.attribution_data == {
            "parent_collection": "atomic_err",
        }

    async def test_on_post_execute_stamps_targeted_harm_categories(self, sample_attack_result, mock_memory):
        """Harm categories from context.params are stamped onto the persisted result."""

        class TestAttackContext(AttackContext):
            pass

        params = AttackParameters(
            objective="Test harmful objective",
            targeted_harm_categories=["violence", "hate"],
        )
        context = TestAttackContext(params=params)
        context.start_time = 100.0

        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()
            event_data = StrategyEventData(
                event=StrategyEvent.ON_POST_EXECUTE,
                strategy_name="TestStrategy",
                strategy_id="test-id",
                context=context,
                result=sample_attack_result,
            )
            await handler.on_event_async(event_data)

        assert sorted(sample_attack_result.targeted_harm_categories) == ["hate", "violence"]

    async def test_on_post_execute_no_harm_categories_leaves_empty(
        self, sample_attack_context, sample_attack_result, mock_memory
    ):
        """With no harm categories on params, the result's list stays empty."""
        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()
            sample_attack_context.start_time = 100.0

            event_data = StrategyEventData(
                event=StrategyEvent.ON_POST_EXECUTE,
                strategy_name="TestStrategy",
                strategy_id="test-id",
                context=sample_attack_context,
                result=sample_attack_result,
            )
            await handler.on_event_async(event_data)

        assert sample_attack_result.targeted_harm_categories == []

    async def test_on_error_stamps_targeted_harm_categories(self, mock_memory):
        """Error AttackResults must also carry the targeted harm categories."""

        class TestAttackContext(AttackContext):
            pass

        params = AttackParameters(
            objective="Test harmful objective",
            targeted_harm_categories=["self_harm"],
        )
        context = TestAttackContext(params=params)
        context.start_time = 100.0

        with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance", return_value=mock_memory):
            handler = _DefaultAttackStrategyEventHandler()
            event_data = StrategyEventData(
                event=StrategyEvent.ON_ERROR,
                strategy_name="TestStrategy",
                strategy_id="test-id",
                context=context,
                error=RuntimeError("boom"),
            )
            await handler.on_event_async(event_data)

        call = mock_memory.add_attack_results_to_memory.call_args
        persisted = call.kwargs["attack_results"][0]
        assert persisted.outcome == AttackOutcome.ERROR
        assert persisted.targeted_harm_categories == ["self_harm"]


@pytest.mark.usefixtures("patch_central_database")
class TestAttackStrategyIntegration:
    """Integration tests for AttackStrategy with event handlers"""

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

    async def test_attack_strategy_with_custom_event_handler(self, mock_objective_target):
        """Test that AttackStrategy can work with custom event handlers"""
        custom_handler_called = False

        class CustomEventHandler:
            async def on_event_async(self, event_data):
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


def _adv_target(*, model_name: str = "gpt-adv", extra_params: dict | None = None) -> PromptTarget:
    """Build a mock adversarial chat target whose identifier carries the given params."""
    target = MagicMock(spec=PromptTarget)
    params: dict = {"model_name": model_name}
    if extra_params:
        params.update(extra_params)
    target.get_identifier.return_value = ComponentIdentifier(class_name="AdvChat", class_module="test", params=params)
    return target


class _IdentityTestStrategy(AttackStrategy):
    """Minimal concrete strategy that exposes a settable adversarial config for identity tests."""

    def __init__(self, *, objective_target, adversarial_config=None):
        super().__init__(context_type=AttackContext, objective_target=objective_target)
        self._test_adversarial_config = adversarial_config

    def _validate_context(self, *, context):
        pass

    async def _setup_async(self, *, context):
        pass

    async def _perform_async(self, *, context):
        return AttackResult(
            conversation_id="c",
            objective="o",
            outcome=AttackOutcome.SUCCESS,
            outcome_reason="ok",
            execution_time_ms=0,
            executed_turns=1,
        )

    async def _teardown_async(self, *, context):
        pass

    def get_attack_adversarial_config(self):
        return self._test_adversarial_config


def _eval_hash(attack_identifier: ComponentIdentifier) -> str:
    composite = AtomicAttackIdentifier.build(attack_identifier=attack_identifier)
    return AtomicAttackEvaluationIdentifier(composite).eval_hash


@pytest.mark.usefixtures("patch_central_database")
class TestCreateIdentifierAdversarial:
    """Tests for adversarial config wiring into the attack identifier (component + eval hash)."""

    def test_base_returns_none_omits_adversarial_child_and_params(self, mock_objective_target):
        """When get_attack_adversarial_config() returns None, no adversarial child/params appear."""
        strategy = _IdentityTestStrategy(objective_target=mock_objective_target, adversarial_config=None)
        identifier = strategy.get_identifier()
        assert "adversarial_chat" not in identifier.children
        assert "adversarial_system_prompt" not in identifier.params
        assert "adversarial_seed_prompt" not in identifier.params

    def test_adversarial_target_added_as_child(self, mock_objective_target):
        adv = _adv_target()
        config = AttackAdversarialConfig(target=adv, system_prompt=None, first_message=None)
        strategy = _IdentityTestStrategy(objective_target=mock_objective_target, adversarial_config=config)
        identifier = strategy.get_identifier()
        assert identifier.children["adversarial_chat"] == adv.get_identifier.return_value

    def test_target_only_config_omits_prompt_params(self, mock_objective_target):
        """A target-only config (no prompts) emits the child but no prompt params."""
        config = AttackAdversarialConfig(target=_adv_target(), system_prompt=None, first_message=None)
        strategy = _IdentityTestStrategy(objective_target=mock_objective_target, adversarial_config=config)
        identifier = strategy.get_identifier()
        assert "adversarial_chat" in identifier.children
        assert "adversarial_system_prompt" not in identifier.params
        assert "adversarial_seed_prompt" not in identifier.params

    def test_system_prompt_string_stored_in_params(self, mock_objective_target):
        config = AttackAdversarialConfig(
            target=_adv_target(), system_prompt="persona {{ objective }}", first_message=None
        )
        strategy = _IdentityTestStrategy(objective_target=mock_objective_target, adversarial_config=config)
        identifier = strategy.get_identifier()
        assert identifier.params["adversarial_system_prompt"] == "persona {{ objective }}"

    def test_first_message_seedprompt_value_stored_in_params(self, mock_objective_target):
        seed = SeedPrompt(value="seed {{ objective }}", data_type="text", parameters=["objective"])
        config = AttackAdversarialConfig(target=_adv_target(), system_prompt=None, first_message=seed)
        strategy = _IdentityTestStrategy(objective_target=mock_objective_target, adversarial_config=config)
        identifier = strategy.get_identifier()
        assert identifier.params["adversarial_seed_prompt"] == "seed {{ objective }}"

    def test_different_system_prompt_changes_full_and_eval_hash(self, mock_objective_target):
        adv = _adv_target()
        s1 = _IdentityTestStrategy(
            objective_target=mock_objective_target,
            adversarial_config=AttackAdversarialConfig(target=adv, system_prompt="persona A", first_message=None),
        )
        s2 = _IdentityTestStrategy(
            objective_target=mock_objective_target,
            adversarial_config=AttackAdversarialConfig(target=adv, system_prompt="persona B", first_message=None),
        )
        id1, id2 = s1.get_identifier(), s2.get_identifier()
        assert id1.hash != id2.hash
        assert _eval_hash(id1) != _eval_hash(id2)

    def test_different_first_message_changes_full_and_eval_hash(self, mock_objective_target):
        adv = _adv_target()
        s1 = _IdentityTestStrategy(
            objective_target=mock_objective_target,
            adversarial_config=AttackAdversarialConfig(target=adv, system_prompt=None, first_message="first A"),
        )
        s2 = _IdentityTestStrategy(
            objective_target=mock_objective_target,
            adversarial_config=AttackAdversarialConfig(target=adv, system_prompt=None, first_message="first B"),
        )
        id1, id2 = s1.get_identifier(), s2.get_identifier()
        assert id1.hash != id2.hash
        assert _eval_hash(id1) != _eval_hash(id2)

    def test_different_adversarial_model_changes_eval_hash(self, mock_objective_target):
        """model_name is in the adversarial_chat eval allowlist -> different eval hash."""
        s1 = _IdentityTestStrategy(
            objective_target=mock_objective_target,
            adversarial_config=AttackAdversarialConfig(
                target=_adv_target(model_name="gpt-4o"), system_prompt=None, first_message=None
            ),
        )
        s2 = _IdentityTestStrategy(
            objective_target=mock_objective_target,
            adversarial_config=AttackAdversarialConfig(
                target=_adv_target(model_name="gpt-3.5"), system_prompt=None, first_message=None
            ),
        )
        assert _eval_hash(s1.get_identifier()) != _eval_hash(s2.get_identifier())

    def test_adversarial_endpoint_does_not_change_eval_hash(self, mock_objective_target):
        """endpoint is NOT in the adversarial_chat eval allowlist -> same eval hash."""
        s1 = _IdentityTestStrategy(
            objective_target=mock_objective_target,
            adversarial_config=AttackAdversarialConfig(
                target=_adv_target(extra_params={"endpoint": "https://a.com"}), system_prompt=None, first_message=None
            ),
        )
        s2 = _IdentityTestStrategy(
            objective_target=mock_objective_target,
            adversarial_config=AttackAdversarialConfig(
                target=_adv_target(extra_params={"endpoint": "https://b.com"}), system_prompt=None, first_message=None
            ),
        )
        assert _eval_hash(s1.get_identifier()) == _eval_hash(s2.get_identifier())

    def test_adversarial_presence_changes_hash_vs_none(self, mock_objective_target):
        """An attack with an adversarial child must not collide with one that has none."""
        plain = _IdentityTestStrategy(objective_target=mock_objective_target, adversarial_config=None)
        adversarial = _IdentityTestStrategy(
            objective_target=mock_objective_target,
            adversarial_config=AttackAdversarialConfig(target=_adv_target(), system_prompt=None, first_message=None),
        )
        assert plain.get_identifier().hash != adversarial.get_identifier().hash
