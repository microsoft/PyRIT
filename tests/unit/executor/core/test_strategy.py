# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass

import pytest

from pyrit.exceptions import (
    ComponentRole,
    clear_execution_context,
    execution_context,
)
from pyrit.executor.core.strategy import Strategy, StrategyContext
from pyrit.identifiers import ComponentIdentifier


@dataclass
class MockContext(StrategyContext):
    """A mock context for testing."""

    value: str = "test"


class MockStrategy(Strategy[MockContext, str]):
    """A mock strategy for testing."""

    def __init__(self, perform_result: str = "success", perform_exception: Exception = None):
        # Initialize base class with the context type
        super().__init__(context_type=MockContext)
        self._perform_result = perform_result
        self._perform_exception = perform_exception

    async def _setup_async(self, *, context: MockContext) -> None:
        pass

    async def _perform_async(self, *, context: MockContext) -> str:
        if self._perform_exception:
            raise self._perform_exception
        return self._perform_result

    async def _teardown_async(self, *, context: MockContext) -> None:
        pass

    def _validate_context(self, *, context: MockContext) -> None:
        pass


@pytest.mark.usefixtures("patch_central_database")
class TestStrategyExecutionContext:
    """Tests for Strategy execution context handling."""

    def teardown_method(self):
        """Clear context after each test."""
        clear_execution_context()

    @pytest.mark.asyncio
    async def test_execute_with_context_success_clears_context(self):
        """Test that successful execution clears execution context."""
        strategy = MockStrategy(perform_result="success")
        context = MockContext()

        # Set a context before execution
        with execution_context(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            attack_strategy_name="TestStrategy",
        ):
            result = await strategy.execute_with_context_async(context=context)

        assert result == "success"
        # Context should be cleared after successful execution
        # (cleared by the context manager on successful exit)

    @pytest.mark.asyncio
    async def test_execute_with_context_exception_includes_context(self):
        """Test that exceptions include execution context details."""
        strategy = MockStrategy(perform_exception=ValueError("Test error"))
        context = MockContext()

        # The strategy wraps the exception with context details
        with pytest.raises(RuntimeError) as exc_info:
            # First set an execution context (simulating what attacks do)
            with execution_context(
                component_role=ComponentRole.OBJECTIVE_TARGET,
                attack_strategy_name="MockStrategy",
                attack_identifier=ComponentIdentifier(
                    class_name="MockStrategy", class_module="test_module", params={"id": "test-123"}
                ),
            ):
                await strategy.execute_with_context_async(context=context)

        error_message = str(exc_info.value)
        # Should include component role
        assert "objective_target" in error_message
        # Should include strategy name
        assert "MockStrategy" in error_message

    @pytest.mark.asyncio
    async def test_execute_with_context_exception_without_context(self):
        """Test that exceptions work even without execution context."""
        strategy = MockStrategy(perform_exception=RuntimeError("Something went wrong"))
        context = MockContext()

        with pytest.raises(RuntimeError) as exc_info:
            await strategy.execute_with_context_async(context=context)

        error_message = str(exc_info.value)
        assert "MockStrategy" in error_message
        assert "Something went wrong" in error_message

    @pytest.mark.asyncio
    async def test_execute_with_context_preserves_root_cause(self):
        """Test that the original exception is preserved as __cause__."""
        original_error = ValueError("Original error")
        strategy = MockStrategy(perform_exception=original_error)
        context = MockContext()

        with pytest.raises(RuntimeError) as exc_info:
            await strategy.execute_with_context_async(context=context)

        # The __cause__ should be the original exception
        assert exc_info.value.__cause__ is original_error

    @pytest.mark.asyncio
    async def test_execute_with_context_extracts_root_cause(self):
        """Test that chained exceptions show root cause in error message."""
        # Create a chain of exceptions
        root_cause = ConnectionError("Connection refused")
        middle_error = RuntimeError("API call failed")
        middle_error.__cause__ = root_cause

        strategy = MockStrategy(perform_exception=middle_error)
        context = MockContext()

        with (
            execution_context(
                component_role=ComponentRole.OBJECTIVE_TARGET,
                attack_strategy_name="MockStrategy",
            ),
            pytest.raises(RuntimeError) as exc_info,
        ):
            await strategy.execute_with_context_async(context=context)

        error_message = str(exc_info.value)
        # Should include root cause information
        assert "Root cause" in error_message
        assert "ConnectionError" in error_message
        assert "Connection refused" in error_message


@pytest.mark.usefixtures("patch_central_database")
class TestStrategyExecutionContextDetails:
    """Tests for execution context detail extraction in strategy errors."""

    def teardown_method(self):
        """Clear context after each test."""
        clear_execution_context()

    @pytest.mark.asyncio
    async def test_error_includes_attack_identifier(self):
        """Test that error message includes attack identifier."""
        strategy = MockStrategy(perform_exception=ValueError("Error"))
        context = MockContext()

        with (
            pytest.raises(RuntimeError) as exc_info,
            execution_context(
                component_role=ComponentRole.ADVERSARIAL_CHAT,
                attack_strategy_name="TestAttack",
                attack_identifier=ComponentIdentifier(
                    class_name="TestAttack", class_module="test_module", params={"id": "abc-123"}
                ),
            ),
        ):
            await strategy.execute_with_context_async(context=context)

        error_message = str(exc_info.value)
        assert "Attack identifier:" in error_message
        assert "TestAttack" in error_message

    @pytest.mark.asyncio
    async def test_error_includes_conversation_id(self):
        """Test that error message includes objective target conversation ID."""
        strategy = MockStrategy(perform_exception=ValueError("Error"))
        context = MockContext()

        with (
            pytest.raises(RuntimeError) as exc_info,
            execution_context(
                component_role=ComponentRole.OBJECTIVE_TARGET,
                attack_strategy_name="TestAttack",
                objective_target_conversation_id="conv-xyz-789",
            ),
        ):
            await strategy.execute_with_context_async(context=context)

        error_message = str(exc_info.value)
        assert "Objective target conversation ID: conv-xyz-789" in error_message

    @pytest.mark.asyncio
    async def test_error_includes_component_identifier(self):
        """Test that error message includes component identifier."""
        strategy = MockStrategy(perform_exception=ValueError("Error"))
        context = MockContext()

        with (
            pytest.raises(RuntimeError) as exc_info,
            execution_context(
                component_role=ComponentRole.OBJECTIVE_SCORER,
                attack_strategy_name="TestAttack",
                component_identifier=ComponentIdentifier(
                    class_name="SelfAskTrueFalseScorer",
                    class_module="pyrit.score.true_false.self_ask_true_false_scorer",
                ),
            ),
        ):
            await strategy.execute_with_context_async(context=context)

        error_message = str(exc_info.value)
        assert "objective_scorer identifier:" in error_message
        assert "SelfAskTrueFalseScorer" in error_message
