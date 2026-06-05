# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import os
from contextlib import suppress

import pytest
from tenacity import RetryError

from pyrit.exceptions import (
    CONTENT_FILTER_MARKERS,
    BadRequestException,
    EmptyResponseException,
    InvalidJsonException,
    MissingPromptPlaceholderException,
    PyritException,
    RateLimitException,
    handle_bad_request_exception,
    pyrit_custom_result_retry,
)
from pyrit.models import MessagePiece


def test_pyrit_exception_initialization():
    ex = PyritException(status_code=500, message="Internal Server Error")
    assert ex.status_code == 500
    assert ex.message == "Internal Server Error"
    assert str(ex) == "Status Code: 500, Message: Internal Server Error"


def test_pyrit_exception_process_exception(caplog):
    ex = PyritException(status_code=500, message="Internal Server Error")
    with caplog.at_level(logging.ERROR):
        result = ex.process_exception()
    assert json.loads(result) == {"status_code": 500, "message": "Internal Server Error"}
    assert "PyritException encountered: Status Code: 500, Message: Internal Server Error" in caplog.text


def test_bad_request_exception_initialization():
    ex = BadRequestException()
    assert ex.status_code == 400
    assert ex.message == "Bad Request"
    assert str(ex) == "Status Code: 400, Message: Bad Request"


def test_rate_limit_exception_initialization():
    ex = RateLimitException()
    assert ex.status_code == 429
    assert ex.message == "Rate Limit Exception"
    assert str(ex) == "Status Code: 429, Message: Rate Limit Exception"


def test_empty_response_exception_initialization():
    ex = EmptyResponseException()
    assert ex.status_code == 204
    assert ex.message == "No Content"
    assert str(ex) == "Status Code: 204, Message: No Content"


def test_invalid_json_exception_initialization():
    ex = InvalidJsonException()
    assert ex.status_code == 500
    assert ex.message == "Invalid JSON Response"
    assert str(ex) == "Status Code: 500, Message: Invalid JSON Response"


def test_bad_request_exception_process_exception(caplog):
    ex = BadRequestException()
    with caplog.at_level(logging.ERROR):
        result = ex.process_exception()
    assert json.loads(result) == {"status_code": 400, "message": "Bad Request"}
    assert "BadRequestException encountered: Status Code: 400, Message: Bad Request" in caplog.text


def test_rate_limit_exception_process_exception(caplog):
    ex = RateLimitException()
    with caplog.at_level(logging.ERROR):
        result = ex.process_exception()
    assert json.loads(result) == {"status_code": 429, "message": "Rate Limit Exception"}
    assert "RateLimitException encountered: Status Code: 429, Message: Rate Limit Exception" in caplog.text


def test_empty_response_exception_process_exception(caplog):
    ex = EmptyResponseException()
    with caplog.at_level(logging.ERROR):
        result = ex.process_exception()
    assert json.loads(result) == {"status_code": 204, "message": "No Content"}
    assert "EmptyResponseException encountered: Status Code: 204, Message: No Content" in caplog.text


def test_empty_prompt_placeholder_exception(caplog):
    ex = MissingPromptPlaceholderException()
    with caplog.at_level(logging.ERROR):
        result = ex.process_exception()
    assert json.loads(result) == {"status_code": 500, "message": "No prompt placeholder"}
    assert (
        "MissingPromptPlaceholderException encountered: Status Code: 500, Message: No prompt placeholder" in caplog.text
    )


def test_remove_markdown_json_exception(caplog):
    ex = InvalidJsonException()
    with caplog.at_level(logging.ERROR):
        result = ex.process_exception()
    assert json.loads(result) == {"status_code": 500, "message": "Invalid JSON Response"}
    assert "InvalidJsonException encountered: Status Code: 500, Message: Invalid JSON Response" in caplog.text


def _make_request_piece() -> MessagePiece:
    return MessagePiece(role="user", conversation_id="test-convo", original_value="hello")


def test_content_filter_markers_exported_from_pyrit_exceptions():
    """The marker set must be importable from ``pyrit.exceptions`` as the single source of truth."""
    assert "content_filter" in CONTENT_FILTER_MARKERS
    assert "moderation_blocked" in CONTENT_FILTER_MARKERS
    assert "policy_violation" in CONTENT_FILTER_MARKERS
    assert "content_safety_violation" in CONTENT_FILTER_MARKERS


@pytest.mark.parametrize(
    "marker_response_text",
    [
        "content_filter",
        '{"error": {"code": "moderation_blocked"}}',
        '{"error": {"code": "content_policy_violation"}}',
        '{"error": {"code": "content_safety_violation"}}',
    ],
)
def test_handle_bad_request_exception_returns_blocked_for_any_marker(marker_response_text):
    """The substring fallback must trigger for every marker in ``CONTENT_FILTER_MARKERS``."""
    try:
        raise RuntimeError("simulated upstream error")
    except RuntimeError:
        response = handle_bad_request_exception(
            response_text=marker_response_text,
            request=_make_request_piece(),
        )

    assert response.message_pieces[0].response_error == "blocked"


def test_handle_bad_request_exception_reraises_when_no_marker_and_not_content_filter():
    """If neither ``is_content_filter`` nor any marker matches, the original exception must propagate."""
    with pytest.raises(RuntimeError, match="simulated upstream error"):
        try:
            raise RuntimeError("simulated upstream error")
        except RuntimeError:
            handle_bad_request_exception(
                response_text='{"error": {"code": "schema_validation_failed"}}',
                request=_make_request_piece(),
            )


def test_handle_bad_request_exception_returns_blocked_when_is_content_filter_true():
    """An explicit ``is_content_filter`` signal must trigger the blocked path regardless of response_text."""
    try:
        raise RuntimeError("simulated upstream error")
    except RuntimeError:
        response = handle_bad_request_exception(
            response_text="some unrelated text without any marker",
            request=_make_request_piece(),
            is_content_filter=True,
        )

    assert response.message_pieces[0].response_error == "blocked"


class TestRetryDecoratorsRespectRuntimeEnvVars:
    """
    Tests that retry decorators read environment variables at runtime, not at decoration time.

    This is critical because users set RETRY_MAX_NUM_ATTEMPTS in their .env file, which is
    loaded by initialize_pyrit_async() AFTER pyrit modules are imported. If decorators
    captured the env var value at import time, the .env settings would be ignored.
    """

    def test_pyrit_target_retry_respects_runtime_env_var(self):
        """Test that pyrit_target_retry reads RETRY_MAX_NUM_ATTEMPTS at runtime."""
        import os

        from pyrit.exceptions import EmptyResponseException, pyrit_target_retry

        call_count = 0

        @pyrit_target_retry
        def failing_function():
            nonlocal call_count
            call_count += 1
            raise EmptyResponseException

        # Change the env var AFTER the decorator has been applied
        original_value = os.environ.get("RETRY_MAX_NUM_ATTEMPTS")
        os.environ["RETRY_MAX_NUM_ATTEMPTS"] = "3"

        with suppress(EmptyResponseException):
            failing_function()

        # Restore original value
        if original_value is not None:
            os.environ["RETRY_MAX_NUM_ATTEMPTS"] = original_value

        # Should have retried 3 times (the runtime value), not the value at decoration time
        assert call_count == 3, (
            f"Expected 3 attempts based on runtime RETRY_MAX_NUM_ATTEMPTS, but got {call_count}. "
            "This suggests the decorator is reading the env var at decoration time, not runtime."
        )

    def test_pyrit_json_retry_respects_runtime_env_var(self):
        """Test that pyrit_json_retry reads RETRY_MAX_NUM_ATTEMPTS at runtime."""
        import os

        from pyrit.exceptions import InvalidJsonException, pyrit_json_retry

        call_count = 0

        @pyrit_json_retry
        def failing_function():
            nonlocal call_count
            call_count += 1
            raise InvalidJsonException

        # Change the env var AFTER the decorator has been applied
        original_value = os.environ.get("RETRY_MAX_NUM_ATTEMPTS")
        os.environ["RETRY_MAX_NUM_ATTEMPTS"] = "4"

        with suppress(InvalidJsonException):
            failing_function()

        # Restore original value
        if original_value is not None:
            os.environ["RETRY_MAX_NUM_ATTEMPTS"] = original_value

        # Should have retried 4 times (the runtime value)
        assert call_count == 4, (
            f"Expected 4 attempts based on runtime RETRY_MAX_NUM_ATTEMPTS, but got {call_count}. "
            "This suggests the decorator is reading the env var at decoration time, not runtime."
        )

    def test_pyrit_placeholder_retry_respects_runtime_env_var(self):
        """Test that pyrit_placeholder_retry reads RETRY_MAX_NUM_ATTEMPTS at runtime."""
        import os

        from pyrit.exceptions import (
            MissingPromptPlaceholderException,
            pyrit_placeholder_retry,
        )

        call_count = 0

        @pyrit_placeholder_retry
        def failing_function():
            nonlocal call_count
            call_count += 1
            raise MissingPromptPlaceholderException

        # Change the env var AFTER the decorator has been applied
        original_value = os.environ.get("RETRY_MAX_NUM_ATTEMPTS")
        os.environ["RETRY_MAX_NUM_ATTEMPTS"] = "3"

        with suppress(MissingPromptPlaceholderException):
            failing_function()

        # Restore original value
        if original_value is not None:
            os.environ["RETRY_MAX_NUM_ATTEMPTS"] = original_value

        # Should have retried 3 times (the runtime value)
        assert call_count == 3, (
            f"Expected 3 attempts based on runtime RETRY_MAX_NUM_ATTEMPTS, but got {call_count}. "
            "This suggests the decorator is reading the env var at decoration time, not runtime."
        )

    def test_pyrit_custom_result_retry_respects_runtime_env_var(self):
        """Test that pyrit_custom_result_retry reads CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS at runtime."""

        call_count = 0

        def should_retry(result):
            return result == "retry"

        @pyrit_custom_result_retry(retry_function=should_retry)
        def failing_function():
            nonlocal call_count
            call_count += 1
            return "retry"

        # Change the env var AFTER the decorator has been applied
        original_value = os.environ.get("CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS")
        os.environ["CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS"] = "3"

        with suppress(RetryError):
            failing_function()

        # Restore original value
        if original_value is not None:
            os.environ["CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS"] = original_value

        # Should have retried 3 times (the runtime value)
        assert call_count == 3, (
            f"Expected 3 attempts based on runtime CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS, but got {call_count}. "
            "This suggests the decorator is reading the env var at decoration time, not runtime."
        )
