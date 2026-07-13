# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exception classes, retry helpers, and execution context utilities."""

from pyrit.exceptions.exception_classes import (
    CONTENT_FILTER_MARKERS,
    BadRequestException,
    EmptyResponseException,
    ExperimentalWarning,
    InvalidJsonException,
    MissingPromptPlaceholderException,
    PyritException,
    RateLimitException,
    ScorerLLMResponseBlockedException,
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
from pyrit.exceptions.retry_collector import (
    RetryCollector,
    clear_retry_collector,
    get_retry_collector,
    set_retry_collector,
)

__all__ = [
    "BadRequestException",
    "clear_execution_context",
    "clear_retry_collector",
    "ComponentRole",
    "CONTENT_FILTER_MARKERS",
    "EmptyResponseException",
    "ExecutionContext",
    "ExecutionContextManager",
    "ExperimentalWarning",
    "get_execution_context",
    "get_retry_collector",
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
    "RetryCollector",
    "ScorerLLMResponseBlockedException",
    "set_execution_context",
    "set_retry_collector",
    "execution_context",
]
