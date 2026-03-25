# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

# Tests for log_exception with execution context
from unittest.mock import MagicMock, patch

import pytest

from pyrit.exceptions import (
    ComponentRole,
    ExecutionContext,
    clear_execution_context,
    set_execution_context,
)
from pyrit.exceptions.exceptions_helpers import (
    extract_json_from_string,
    log_exception,
    remove_end_md_json,
    remove_markdown_json,
    remove_start_md_json,
)


@pytest.mark.parametrize(
    "input_str, expected_output",
    [
        ('```json\n{"key": "value"}', '{"key": "value"}'),
        ('`json\n{"key": "value"}', '{"key": "value"}'),
        ('json{"key": "value"}', '{"key": "value"}'),
        ('{"key": "value"}', '{"key": "value"}'),
        ("No JSON here", "No JSON here"),
        ('```jsn\n{"key": "value"}\n```', 'jsn\n{"key": "value"}\n```'),
    ],
)
def test_remove_start_md_json(input_str, expected_output):
    assert remove_start_md_json(input_str) == expected_output


@pytest.mark.parametrize(
    "input_str, expected_output",
    [
        ('{"key": "value"}\n```', '{"key": "value"}'),
        ('{"key": "value"}\n`', '{"key": "value"}'),
        ('{"key": "value"}`', '{"key": "value"}'),
        ('{"key": "value"}', '{"key": "value"}'),
    ],
)
def test_remove_end_md_json(input_str, expected_output):
    assert remove_end_md_json(input_str) == expected_output


@pytest.mark.parametrize(
    "input_str, expected_output",
    [
        ('Some text before JSON {"key": "value"} some text after JSON', '{"key": "value"}'),
        ("Some text before JSON [1, 2, 3] some text after JSON", "[1, 2, 3]"),
        ('{"key": "value"}', '{"key": "value"}'),
        ("[1, 2, 3]", "[1, 2, 3]"),
        ("No JSON here", "No JSON here"),
        ('jsn\n{"key": "value"}\n```', '{"key": "value"}'),
        ('Some text before JSON {"a": [1,2,3], "b": {"c": 4}} some text after JSON', '{"a": [1,2,3], "b": {"c": 4}}'),
        (
            'Some text before JSON {\n  "key": "value",\n  "nested": {"enabled": true}\n} some text after JSON',
            '{\n  "key": "value",\n  "nested": {"enabled": true}\n}',
        ),
    ],
)
def test_extract_json_from_string(input_str, expected_output):
    assert extract_json_from_string(input_str) == expected_output


@pytest.mark.parametrize(
    "input_str, expected_output",
    [
        ('```json\n{"key": "value"}\n```', '{"key": "value"}'),
        ('```json\n{"key": "value"}', '{"key": "value"}'),
        ('{"key": "value"}\n```', '{"key": "value"}'),
        ('Some text before JSON ```json\n{"key": "value"}\n``` some text after JSON', '{"key": "value"}'),
        ('```json\n{"key": "value"\n```', 'Invalid JSON response: {"key": "value"'),
        ("No JSON here", "Invalid JSON response: No JSON here"),
        ('```jsn\n{"key": "value"}\n```', '{"key": "value"}'),
    ],
)
def test_remove_markdown_json(input_str, expected_output):
    assert remove_markdown_json(input_str) == expected_output


class TestLogException:
    """Tests for the log_exception function with execution context."""

    def teardown_method(self):
        """Clear context after each test."""
        clear_execution_context()

    def test_log_exception_without_context(self):
        """Test log_exception works when no execution context is set."""
        # Create a mock retry state
        retry_state = MagicMock()
        retry_state.attempt_number = 2
        retry_state.start_time = 0.0

        # Create a failed outcome with an exception
        outcome = MagicMock()
        outcome.failed = True
        outcome.exception.return_value = ValueError("Test error")
        retry_state.outcome = outcome

        retry_state.fn = MagicMock()
        retry_state.fn.__name__ = "test_function"

        with patch("pyrit.exceptions.exceptions_helpers.logger") as mock_logger:
            log_exception(retry_state)
            mock_logger.error.assert_called_once()
            call_args = mock_logger.error.call_args[0][0]
            assert "test_function" in call_args
            assert "Test error" in call_args
            # Should just have function name when no context is set
            assert "objective target" not in call_args

    def test_log_exception_with_context_and_component_name(self):
        """Test log_exception includes component role and class name when set."""
        # Set execution context with component name
        context = ExecutionContext(
            component_role=ComponentRole.OBJECTIVE_SCORER,
            component_name="TrueFalseScorer",
            endpoint="https://api.openai.com",
        )
        set_execution_context(context)

        # Create a mock retry state
        retry_state = MagicMock()
        retry_state.attempt_number = 3
        retry_state.start_time = 0.0

        outcome = MagicMock()
        outcome.failed = True
        outcome.exception.return_value = ConnectionError("Connection failed")
        retry_state.outcome = outcome

        retry_state.fn = MagicMock()
        retry_state.fn.__name__ = "_score_value_with_llm"

        with patch("pyrit.exceptions.exceptions_helpers.logger") as mock_logger:
            log_exception(retry_state)
            mock_logger.error.assert_called_once()
            call_args = mock_logger.error.call_args[0][0]
            # New format: "objective scorer; TrueFalseScorer::_score_value_with_llm"
            assert "objective scorer" in call_args
            assert "TrueFalseScorer::_score_value_with_llm" in call_args
            assert "Connection failed" in call_args

    def test_log_exception_with_context_no_component_name(self):
        """Test log_exception with context but no component name."""
        context = ExecutionContext(component_role=ComponentRole.CONVERTER)
        set_execution_context(context)

        retry_state = MagicMock()
        retry_state.attempt_number = 1
        retry_state.start_time = 0.0

        outcome = MagicMock()
        outcome.failed = True
        outcome.exception.return_value = RuntimeError("Conversion failed")
        retry_state.outcome = outcome

        retry_state.fn = MagicMock()
        retry_state.fn.__name__ = "convert_async"

        with patch("pyrit.exceptions.exceptions_helpers.logger") as mock_logger:
            log_exception(retry_state)
            call_args = mock_logger.error.call_args[0][0]
            # Without component name: "converter; convert_async"
            assert "converter. convert_async" in call_args
            # Should not have "::" since no component name
            assert "::" not in call_args

    def test_log_exception_no_retry_state(self):
        """Test log_exception handles None retry_state gracefully."""
        with patch("pyrit.exceptions.exceptions_helpers.logger") as mock_logger:
            log_exception(None)
            mock_logger.error.assert_called_once()
            assert "no retry state" in mock_logger.error.call_args[0][0].lower()

    def test_log_exception_no_outcome(self):
        """Test log_exception handles missing outcome gracefully."""
        retry_state = MagicMock()
        retry_state.outcome = None

        with patch("pyrit.exceptions.exceptions_helpers.logger") as mock_logger:
            log_exception(retry_state)
            # Should return early without logging error details
            mock_logger.error.assert_not_called()

    def test_log_exception_outcome_not_failed(self):
        """Test log_exception doesn't log when outcome is not failed."""
        retry_state = MagicMock()
        retry_state.attempt_number = 1
        outcome = MagicMock()
        outcome.failed = False
        retry_state.outcome = outcome

        with patch("pyrit.exceptions.exceptions_helpers.logger") as mock_logger:
            log_exception(retry_state)
            mock_logger.error.assert_not_called()
