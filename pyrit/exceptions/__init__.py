# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Exception classes, retry helpers, and execution context utilities."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.exceptions.exception_classes import (
        CONTENT_FILTER_MARKERS,
        BadRequestException,
        EmptyResponseException,
        ExperimentalWarning,
        InvalidJsonException,
        KeyVaultInitializationException,
        MissingPromptPlaceholderException,
        PyritException,
        RateLimitException,
        ScenarioPartialFailureException,
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

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "BadRequestException": "pyrit.exceptions.exception_classes",
    "clear_execution_context": "pyrit.exceptions.exception_context",
    "clear_retry_collector": "pyrit.exceptions.retry_collector",
    "ComponentRole": "pyrit.exceptions.exception_context",
    "CONTENT_FILTER_MARKERS": "pyrit.exceptions.exception_classes",
    "EmptyResponseException": "pyrit.exceptions.exception_classes",
    "ExecutionContext": "pyrit.exceptions.exception_context",
    "ExecutionContextManager": "pyrit.exceptions.exception_context",
    "ExperimentalWarning": "pyrit.exceptions.exception_classes",
    "get_execution_context": "pyrit.exceptions.exception_context",
    "get_retry_collector": "pyrit.exceptions.retry_collector",
    "get_retry_max_num_attempts": "pyrit.exceptions.exception_classes",
    "handle_bad_request_exception": "pyrit.exceptions.exception_classes",
    "InvalidJsonException": "pyrit.exceptions.exception_classes",
    "KeyVaultInitializationException": "pyrit.exceptions.exception_classes",
    "MissingPromptPlaceholderException": "pyrit.exceptions.exception_classes",
    "PyritException": "pyrit.exceptions.exception_classes",
    "pyrit_custom_result_retry": "pyrit.exceptions.exception_classes",
    "pyrit_json_retry": "pyrit.exceptions.exception_classes",
    "pyrit_target_retry": "pyrit.exceptions.exception_classes",
    "pyrit_placeholder_retry": "pyrit.exceptions.exception_classes",
    "RateLimitException": "pyrit.exceptions.exception_classes",
    "remove_markdown_json": "pyrit.exceptions.exceptions_helpers",
    "RetryCollector": "pyrit.exceptions.retry_collector",
    "ScenarioPartialFailureException": "pyrit.exceptions.exception_classes",
    "ScorerLLMResponseBlockedException": "pyrit.exceptions.exception_classes",
    "set_execution_context": "pyrit.exceptions.exception_context",
    "set_retry_collector": "pyrit.exceptions.retry_collector",
    "execution_context": "pyrit.exceptions.exception_context",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
