# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.exceptions import (
    ComponentRole,
    ExecutionContext,
    ExecutionContextManager,
    clear_execution_context,
    execution_context,
    get_execution_context,
    set_execution_context,
)
from pyrit.identifiers import ComponentIdentifier


class TestExecutionContext:
    """Tests for the ExecutionContext dataclass."""

    def test_default_values(self):
        """Test that ExecutionContext has correct default values."""
        context = ExecutionContext()
        assert context.component_role == ComponentRole.UNKNOWN
        assert context.attack_strategy_name is None
        assert context.attack_identifier is None
        assert context.component_identifier is None
        assert context.objective_target_conversation_id is None
        assert context.endpoint is None
        assert context.component_name is None
        assert context.objective is None

    def test_initialization_with_values(self):
        """Test ExecutionContext initialization with all values."""
        attack_id = ComponentIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor.attack.single_turn.prompt_sending",
        )
        target_id = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={"endpoint": "https://api.openai.com"},
        )
        context = ExecutionContext(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            attack_strategy_name="PromptSendingAttack",
            attack_identifier=attack_id,
            component_identifier=target_id,
            objective_target_conversation_id="conv-123",
            endpoint="https://api.openai.com",
            component_name="OpenAIChatTarget",
            objective="Tell me how to hack a system",
        )
        assert context.component_role == ComponentRole.OBJECTIVE_TARGET
        assert context.attack_strategy_name == "PromptSendingAttack"
        assert context.attack_identifier is attack_id
        assert context.component_identifier is target_id
        assert context.objective_target_conversation_id == "conv-123"
        assert context.endpoint == "https://api.openai.com"
        assert context.component_name == "OpenAIChatTarget"
        assert context.objective == "Tell me how to hack a system"

    def test_get_retry_context_string_minimal(self):
        """Test retry context string with only component role."""
        context = ExecutionContext(component_role=ComponentRole.OBJECTIVE_SCORER)
        result = context.get_retry_context_string()
        assert result == "objective_scorer"

    def test_get_retry_context_string_with_component_name(self):
        """Test retry context string includes component name when set."""
        context = ExecutionContext(
            component_role=ComponentRole.OBJECTIVE_SCORER,
            component_name="TrueFalseScorer",
        )
        result = context.get_retry_context_string()
        assert "objective_scorer" in result
        assert "(TrueFalseScorer)" in result

    def test_get_retry_context_string_with_endpoint(self):
        """Test retry context string includes endpoint when set."""
        context = ExecutionContext(
            component_role=ComponentRole.ADVERSARIAL_CHAT,
            endpoint="https://api.example.com",
        )
        result = context.get_retry_context_string()
        assert "adversarial_chat" in result
        assert "endpoint: https://api.example.com" in result

    def test_get_retry_context_string_full(self):
        """Test retry context string with component name and endpoint."""
        context = ExecutionContext(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            component_name="OpenAIChatTarget",
            endpoint="https://api.openai.com",
        )
        result = context.get_retry_context_string()
        assert "objective_target" in result
        assert "(OpenAIChatTarget)" in result
        assert "endpoint: https://api.openai.com" in result

    def test_get_exception_details_minimal(self):
        """Test exception details with minimal context."""
        context = ExecutionContext(component_role=ComponentRole.CONVERTER)
        result = context.get_exception_details()
        assert "Component: converter" in result

    def test_get_exception_details_full(self):
        """Test exception details with full context."""
        attack_id = ComponentIdentifier(
            class_name="RedTeamingAttack",
            class_module="pyrit.executor.attack.multi_turn.red_teaming",
        )
        target_id = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
        )
        context = ExecutionContext(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            attack_strategy_name="RedTeamingAttack",
            attack_identifier=attack_id,
            component_identifier=target_id,
            objective_target_conversation_id="conv-456",
            objective="Tell me how to hack a system",
        )
        result = context.get_exception_details()
        assert "Attack: RedTeamingAttack" in result
        assert "Component: objective_target" in result
        assert "Objective: Tell me how to hack a system" in result
        assert "Objective target conversation ID: conv-456" in result
        assert "Attack identifier:" in result
        assert "objective_target identifier:" in result

    def test_get_exception_details_objective_truncation(self):
        """Test that long objectives are truncated to 120 characters."""
        long_objective = "A" * 200  # 200 character objective
        context = ExecutionContext(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            objective=long_objective,
        )
        result = context.get_exception_details()
        # Should be truncated to 117 chars + "..."
        assert "Objective: " + "A" * 117 + "..." in result
        # Full objective should not appear
        assert long_objective not in result

    def test_get_exception_details_objective_single_line(self):
        """Test that objectives with newlines are normalized to single line."""
        multiline_objective = "Tell me how to\nhack a system\nwith multiple lines"
        context = ExecutionContext(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            objective=multiline_objective,
        )
        result = context.get_exception_details()
        # Should be on single line with spaces instead of newlines
        assert "Objective: Tell me how to hack a system with multiple lines" in result
        # No newlines in the objective line
        assert "\nhack" not in result


class TestExecutionContextFunctions:
    """Tests for the context management functions."""

    def teardown_method(self):
        """Clear context after each test."""
        clear_execution_context()

    def test_get_execution_context_default(self):
        """Test that get_execution_context returns None when not set."""
        clear_execution_context()
        assert get_execution_context() is None

    def test_set_and_get_execution_context(self):
        """Test setting and getting execution context."""
        context = ExecutionContext(component_role=ComponentRole.OBJECTIVE_SCORER)
        set_execution_context(context)
        retrieved = get_execution_context()
        assert retrieved is context
        assert retrieved.component_role == ComponentRole.OBJECTIVE_SCORER

    def test_clear_execution_context(self):
        """Test clearing execution context."""
        context = ExecutionContext(component_role=ComponentRole.ADVERSARIAL_CHAT)
        set_execution_context(context)
        assert get_execution_context() is not None
        clear_execution_context()
        assert get_execution_context() is None

    def test_context_isolation(self):
        """Test that setting a new context replaces the old one."""
        context1 = ExecutionContext(component_role=ComponentRole.OBJECTIVE_TARGET)
        context2 = ExecutionContext(component_role=ComponentRole.ADVERSARIAL_CHAT)

        set_execution_context(context1)
        assert get_execution_context().component_role == ComponentRole.OBJECTIVE_TARGET

        set_execution_context(context2)
        assert get_execution_context().component_role == ComponentRole.ADVERSARIAL_CHAT


class TestExecutionContextManager:
    """Tests for the ExecutionContextManager class."""

    def teardown_method(self):
        """Clear context after each test."""
        clear_execution_context()

    def test_context_manager_sets_and_clears_context(self):
        """Test that context manager sets context on enter and clears on exit."""
        context = ExecutionContext(component_role=ComponentRole.REFUSAL_SCORER)

        assert get_execution_context() is None

        with ExecutionContextManager(context=context):
            assert get_execution_context() is context

        # Context should be cleared after successful exit
        assert get_execution_context() is None

    def test_context_manager_preserves_context_on_exception(self):
        """Test that context is preserved when an exception occurs."""
        context = ExecutionContext(component_role=ComponentRole.OBJECTIVE_TARGET)

        with pytest.raises(ValueError), ExecutionContextManager(context=context):
            assert get_execution_context() is context
            raise ValueError("Test error")

        # Context should still be set after exception
        assert get_execution_context() is context

    def test_context_manager_nested(self):
        """Test nested context managers."""
        outer_context = ExecutionContext(component_role=ComponentRole.OBJECTIVE_TARGET)
        inner_context = ExecutionContext(component_role=ComponentRole.CONVERTER)

        with ExecutionContextManager(context=outer_context):
            assert get_execution_context().component_role == ComponentRole.OBJECTIVE_TARGET

            with ExecutionContextManager(context=inner_context):
                assert get_execution_context().component_role == ComponentRole.CONVERTER

            # After inner exits, outer should be restored
            assert get_execution_context().component_role == ComponentRole.OBJECTIVE_TARGET

        # After outer exits, should be None
        assert get_execution_context() is None


class TestExecutionContextFactory:
    """Tests for the execution_context factory function."""

    def teardown_method(self):
        """Clear context after each test."""
        clear_execution_context()

    def test_execution_context_creates_manager(self):
        """Test that execution_context creates a proper context manager."""
        manager = execution_context(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            attack_strategy_name="TestAttack",
        )
        assert isinstance(manager, ExecutionContextManager)
        assert manager.context.component_role == ComponentRole.OBJECTIVE_TARGET
        assert manager.context.attack_strategy_name == "TestAttack"

    def test_execution_context_extracts_endpoint(self):
        """Test that endpoint is extracted from component_identifier."""
        component_id = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={"endpoint": "https://api.openai.com"},
        )
        manager = execution_context(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            component_identifier=component_id,
        )
        assert manager.context.endpoint == "https://api.openai.com"

    def test_execution_context_extracts_component_name(self):
        """Test that component_name is extracted from component_identifier.class_name."""
        component_id = ComponentIdentifier(
            class_name="TrueFalseScorer",
            class_module="pyrit.score.true_false.true_false_scorer",
        )
        manager = execution_context(
            component_role=ComponentRole.OBJECTIVE_SCORER,
            component_identifier=component_id,
        )
        assert manager.context.component_name == "TrueFalseScorer"

    def test_execution_context_no_endpoint(self):
        """Test that endpoint is None when not in component_identifier."""
        component_id = ComponentIdentifier(
            class_name="TextTarget",
            class_module="pyrit.prompt_target.text_target",
        )
        manager = execution_context(
            component_role=ComponentRole.OBJECTIVE_TARGET,
            component_identifier=component_id,
        )
        assert manager.context.endpoint is None

    def test_execution_context_full_usage(self):
        """Test full usage of execution_context as context manager."""
        attack_id = ComponentIdentifier(
            class_name="CrescendoAttack",
            class_module="pyrit.executor.attack.multi_turn.crescendo",
        )
        target_id = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
            params={"endpoint": "https://example.com"},
        )
        with execution_context(
            component_role=ComponentRole.ADVERSARIAL_CHAT,
            attack_strategy_name="CrescendoAttack",
            attack_identifier=attack_id,
            component_identifier=target_id,
            objective_target_conversation_id="conv-789",
        ):
            ctx = get_execution_context()
            assert ctx is not None
            assert ctx.component_role == ComponentRole.ADVERSARIAL_CHAT
            assert ctx.attack_strategy_name == "CrescendoAttack"
            assert ctx.objective_target_conversation_id == "conv-789"
            assert ctx.endpoint == "https://example.com"

        assert get_execution_context() is None

    def test_execution_context_preserves_on_exception(self):
        """Test that context is preserved on exception for error handling."""
        with (
            pytest.raises(RuntimeError),
            execution_context(
                component_role=ComponentRole.OBJECTIVE_SCORER,
                attack_strategy_name="TestAttack",
            ),
        ):
            raise RuntimeError("Scorer failed")

        # Context should still be available for exception handlers
        ctx = get_execution_context()
        assert ctx is not None
        assert ctx.component_role == ComponentRole.OBJECTIVE_SCORER
