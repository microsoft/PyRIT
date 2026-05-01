# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for PyRIT exception types and retry decorators used by azure-ai-evaluation.

The azure-ai-evaluation red team module uses these in:
- _callback_chat_target.py: EmptyResponseException, RateLimitException, pyrit_target_retry
- _rai_service_target.py: remove_markdown_json
"""

from pyrit.exceptions import (
    EmptyResponseException,
    RateLimitException,
    pyrit_target_retry,
    remove_markdown_json,
)


class TestExceptionTypesContract:
    """Validate exception types exist and are proper Exception subclasses."""

    def test_empty_response_exception_is_exception(self):
        """_CallbackChatTarget catches EmptyResponseException."""
        assert issubclass(EmptyResponseException, Exception)

    def test_rate_limit_exception_is_exception(self):
        """_CallbackChatTarget catches RateLimitException."""
        assert issubclass(RateLimitException, Exception)

    def test_empty_response_exception_instantiable(self):
        """Verify EmptyResponseException can be raised with a message."""
        exc = EmptyResponseException()
        assert isinstance(exc, Exception)

    def test_rate_limit_exception_instantiable(self):
        """Verify RateLimitException can be raised with a message."""
        exc = RateLimitException()
        assert isinstance(exc, Exception)


class TestRetryDecoratorContract:
    """Validate retry decorator availability."""

    def test_pyrit_target_retry_is_callable(self):
        """_CallbackChatTarget uses @pyrit_target_retry decorator."""
        assert callable(pyrit_target_retry)


class TestUtilityFunctionsContract:
    """Validate utility functions used by azure-ai-evaluation."""

    def test_remove_markdown_json_is_callable(self):
        """_rai_service_target.py uses remove_markdown_json."""
        assert callable(remove_markdown_json)

    def test_remove_markdown_json_handles_plain_text(self):
        """Verify remove_markdown_json passes through plain text."""
        result = remove_markdown_json("plain text")
        assert isinstance(result, str)

    def test_remove_markdown_json_strips_markdown_fences(self):
        """Verify remove_markdown_json strips ```json fences."""
        input_text = '```json\n{"key": "value"}\n```'
        result = remove_markdown_json(input_text)
        assert "```" not in result
