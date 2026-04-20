# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exception classes, retry helpers, and execution context utilities."""

from pyrit.exceptions.exception_classes import (
    BadRequestException,
    EmptyResponseException,
    InvalidJsonException,
    MissingPromptPlaceholderException,
    PyritException,
    RateLimitException,
    get_retry_max_num_attempts,
    handle_bad_request_exception,
    pyrit_custom_result_retry,
    pyrit_json_retry,
    pyrit_placeholder_retry,
    pyrit_target_retry,
)
from pyrit.exceptions.exception_context import (
    ComponentRole,
    ExecutionContext,
    ExecutionContextManager,
    clear_execution_context,
    execution_context,
    get_execution_context,
    set_execution_context,
)
from pyrit.exceptions.exceptions_helpers import remove_markdown_json

__all__ = [
    "BadRequestException",
    "clear_execution_context",
    "ComponentRole",
    "EmptyResponseException",
    "ExecutionContext",
    "ExecutionContextManager",
    "get_execution_context",
    "get_retry_max_num_attempts",
    "handle_bad_request_exception",
    "InvalidJsonException",
    "MissingPromptPlaceholderException",
    "PyritException",
    "pyrit_custom_result_retry",
    "pyrit_json_retry",
    "pyrit_target_retry",
    "pyrit_placeholder_retry",
    "RateLimitException",
    "remove_markdown_json",
    "set_execution_context",
    "execution_context",
]
