# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import os
from abc import ABC
from collections.abc import Callable
from typing import Any

from openai import RateLimitError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_random_exponential,
)
from tenacity.stop import stop_base
from tenacity.wait import wait_base

from pyrit.exceptions.exceptions_helpers import log_exception
from pyrit.models import Message, MessagePiece, construct_response_from_request

logger = logging.getLogger(__name__)


def _get_custom_result_retry_max_num_attempts() -> int:
    """
    Get the maximum number of retry attempts for custom result retry decorator.

    Returns:
        int: Maximum retry attempts.

    """
    return int(os.getenv("CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS", 10))


def get_retry_max_num_attempts() -> int:
    """
    Get the maximum number of retry attempts.

    Returns:
        int: Maximum retry attempts.

    """
    return int(os.getenv("RETRY_MAX_NUM_ATTEMPTS", 10))


def _get_retry_wait_min_seconds() -> int:
    """
    Get the minimum wait time in seconds between retries.

    Returns:
        int: Minimum wait duration in seconds.

    """
    return int(os.getenv("RETRY_WAIT_MIN_SECONDS", 5))


def _get_retry_wait_max_seconds() -> int:
    """
    Get the maximum wait time in seconds between retries.

    Returns:
        int: Maximum wait duration in seconds.

    """
    return int(os.getenv("RETRY_WAIT_MAX_SECONDS", 220))


class _DynamicStopAfterAttempt(stop_base):
    """
    A stop strategy that reads the max attempts from environment at runtime.

    Unlike stop_after_attempt which reads the value once at decoration time,
    this class reads the environment variable on each retry check, allowing
    the value to be set after module import (e.g., via initialize_pyrit_async).
    """

    def __init__(self, max_attempts_getter: Callable[[], int]) -> None:
        self._max_attempts_getter = max_attempts_getter

    def __call__(self, retry_state: RetryCallState) -> bool:
        return retry_state.attempt_number >= self._max_attempts_getter()


class _DynamicWaitRandomExponential(wait_base):
    """
    A wait strategy that reads min/max wait times from environment at runtime.

    Unlike wait_random_exponential which reads values once at decoration time,
    this class reads environment variables on each wait calculation, allowing
    values to be set after module import (e.g., via initialize_pyrit_async).
    """

    def __init__(
        self,
        min_seconds_getter: Callable[[], int],
        max_seconds_getter: Callable[[], int],
    ) -> None:
        self._min_seconds_getter = min_seconds_getter
        self._max_seconds_getter = max_seconds_getter

    def __call__(self, retry_state: RetryCallState) -> float:
        # Create a new wait_random_exponential instance with current env values
        # This ensures we always use the latest configuration
        wait_strategy = wait_random_exponential(
            min=self._min_seconds_getter(),
            max=self._max_seconds_getter(),
        )
        return wait_strategy(retry_state)


class PyritException(Exception, ABC):  # noqa: N818
    """Base exception class for PyRIT components."""

    def __init__(self, *, status_code: int = 500, message: str = "An error occurred") -> None:
        """
        Initialize a PyritException.

        Args:
            status_code (int): HTTP-style status code associated with the error.
            message (str): Human-readable error description.

        """
        self.status_code = status_code
        self.message = message
        super().__init__(f"Status Code: {status_code}, Message: {message}")

    def process_exception(self) -> str:
        """
        Log and return a JSON string representation of the exception.

        Returns:
            str: Serialized status code and message.

        """
        log_message = f"{self.__class__.__name__} encountered: Status Code: {self.status_code}, Message: {self.message}"
        logger.error(log_message)
        # Return a string representation of the exception so users can extract and parse
        return json.dumps({"status_code": self.status_code, "message": self.message})


class BadRequestException(PyritException):
    """Exception class for bad client requests."""

    def __init__(self, *, status_code: int = 400, message: str = "Bad Request") -> None:
        """
        Initialize a bad request exception.

        Args:
            status_code (int): Status code for the error.
            message (str): Error message.

        """
        super().__init__(status_code=status_code, message=message)


class RateLimitException(PyritException):
    """Exception class for authentication errors."""

    def __init__(self, *, status_code: int = 429, message: str = "Rate Limit Exception") -> None:
        """
        Initialize a rate limit exception.

        Args:
            status_code (int): Status code for the error.
            message (str): Error message.

        """
        super().__init__(status_code=status_code, message=message)


class ServerErrorException(PyritException):
    """Exception class for opaque 5xx errors returned by the server."""

    def __init__(self, *, status_code: int = 500, message: str = "Server Error", body: str | None = None) -> None:
        """
        Initialize a server error exception.

        Args:
            status_code (int): Status code for the error.
            message (str): Error message.
            body (str | None): Optional raw server response body.

        """
        super().__init__(status_code=status_code, message=message)
        self.body = body


class EmptyResponseException(BadRequestException):
    """Exception class for empty response errors."""

    def __init__(self, *, status_code: int = 204, message: str = "No Content") -> None:
        """
        Initialize an empty response exception.

        Args:
            status_code (int): Status code for the error.
            message (str): Error message.

        """
        super().__init__(status_code=status_code, message=message)


class ScorerLLMResponseBlockedException(BadRequestException):
    """Exception raised when a scorer's own LLM response is blocked by content filtering."""

    def __init__(self, *, status_code: int = 400, message: str = "Scorer LLM response blocked") -> None:
        """
        Initialize a scorer-response-blocked exception.

        Args:
            status_code (int): Status code for the error.
            message (str): Error message.

        """
        super().__init__(status_code=status_code, message=message)


class InvalidJsonException(PyritException):
    """Exception class for blocked content errors."""

    def __init__(self, *, message: str = "Invalid JSON Response") -> None:
        """
        Initialize an invalid JSON exception.

        Args:
            message (str): Error message.

        """
        super().__init__(message=message)


class MissingPromptPlaceholderException(PyritException):
    """Exception class for missing prompt placeholder errors."""

    def __init__(self, *, message: str = "No prompt placeholder") -> None:
        """
        Initialize a missing placeholder exception.

        Args:
            message (str): Error message.

        """
        super().__init__(message=message)


class ExperimentalWarning(FutureWarning):
    """
    Warning category for experimental PyRIT modules whose APIs may change at any time.

    Modules emitting this warning are not covered by PyRIT's normal deprecation policy.
    To silence it, filter the category before importing the experimental module::

        import warnings
        from pyrit.exceptions import ExperimentalWarning
        warnings.filterwarnings("ignore", category=ExperimentalWarning)
    """


def pyrit_custom_result_retry(
    retry_function: Callable[..., bool], retry_max_num_attempts: int | None = None
) -> Callable[..., Any]:
    """
    Apply retry logic with exponential backoff to a function.

    Retries the function if the result of the retry_function is True,
    with a wait time between retries that follows an exponential backoff strategy.
    Logs retry attempts at the INFO level and stops after a maximum number of attempts.

    Args:
        retry_function (Callable): The boolean function to determine if a retry should occur based
            on the result of the decorated function.
        retry_max_num_attempts (Optional, int): The maximum number of retry attempts. Defaults to
            environment variable CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS or 10.

    Returns:
        Callable: The decorated function with retry logic applied.

    """

    def inner_retry(func: Callable[..., Any]) -> Callable[..., Any]:
        # Use static value if explicitly provided, otherwise use dynamic getter
        stop_strategy: stop_base
        if retry_max_num_attempts is not None:
            stop_strategy = stop_after_attempt(retry_max_num_attempts)
        else:
            stop_strategy = _DynamicStopAfterAttempt(_get_custom_result_retry_max_num_attempts)

        return retry(
            reraise=True,
            retry=retry_if_result(retry_function),
            wait=_DynamicWaitRandomExponential(_get_retry_wait_min_seconds, _get_retry_wait_max_seconds),
            after=log_exception,
            stop=stop_strategy,
        )(func)

    return inner_retry


def pyrit_target_retry(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Apply retry logic with exponential backoff to a function.

    Retries the function if it raises RateLimitError or EmptyResponseException,
    with a wait time between retries that follows an exponential backoff strategy.
    Logs retry attempts at the INFO level and stops after a maximum number of attempts.

    Args:
        func (Callable): The function to be decorated.

    Returns:
        Callable: The decorated function with retry logic applied.

    """
    return retry(
        reraise=True,
        retry=retry_if_exception_type(RateLimitError)
        | retry_if_exception_type(EmptyResponseException)
        | retry_if_exception_type(RateLimitException),
        wait=_DynamicWaitRandomExponential(_get_retry_wait_min_seconds, _get_retry_wait_max_seconds),
        after=log_exception,
        stop=_DynamicStopAfterAttempt(get_retry_max_num_attempts),
    )(func)


def pyrit_json_retry(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Apply retry logic to a function.

    Retries the function if it raises a JSON error.
    Logs retry attempts at the INFO level and stops after a maximum number of attempts.

    Args:
        func (Callable): The function to be decorated.

    Returns:
        Callable: The decorated function with retry logic applied.

    """
    return retry(
        reraise=True,
        retry=retry_if_exception_type(InvalidJsonException),
        after=log_exception,
        stop=_DynamicStopAfterAttempt(get_retry_max_num_attempts),
    )(func)


def pyrit_placeholder_retry(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Apply retry logic.

    Retries the function if it raises MissingPromptPlaceholderException.
    Logs retry attempts at the INFO level and stops after a maximum number of attempts.

    Args:
        func (Callable): The function to be decorated.

    Returns:
        Callable: The decorated function with retry logic applied.

    """
    return retry(
        reraise=True,
        retry=retry_if_exception_type(MissingPromptPlaceholderException),
        after=log_exception,
        stop=_DynamicStopAfterAttempt(get_retry_max_num_attempts),
    )(func)


# Empirically-observed markers in OpenAI / Azure OpenAI / MAI error payloads that
# indicate the response was blocked by a content filter or safety system.
#
# There is no canonical spec for these - providers expose the signal through
# different field names (``error.code``, ``finish_reason``, ``incomplete_details.reason``,
# free-form ``error.message``) and the exact wording evolves over time. Rather than
# try to track every (provider, field) combination as an exact match, we scan the
# entire payload as a substring search for resilience: adding support for a new
# provider variant is then a one-line change to the set below.
#
# Each marker below is justified by a concrete provider response shape:
#   - ``content_filter``           - OpenAI ``finish_reason``; Azure ``error.code``;
#                                    Azure ``content_filter_results`` field name.
#   - ``content_safety_violation`` - MAI image models ``error.code`` (added in PR #1890).
#   - ``policy_violation``         - Substring of Azure's ``content_policy_violation``
#                                    and OpenAI moderation's ``usage_policy_violation``.
#   - ``moderation_blocked``       - OpenAI moderation ``error.code``.
CONTENT_FILTER_MARKERS = frozenset(
    {
        "content_filter",
        "content_safety_violation",
        "policy_violation",
        "moderation_blocked",
    }
)


def handle_bad_request_exception(
    response_text: str,
    request: MessagePiece,
    is_content_filter: bool = False,
    error_code: int = 400,
) -> Message:
    """
    Handle bad request responses and map them to standardized error messages.

    The content-filter fallback substring-scans ``response_text`` against
    ``CONTENT_FILTER_MARKERS`` so callers that do not pre-compute
    ``is_content_filter`` (e.g. ``azure_ml_chat_target``) still benefit from
    the full marker set.

    Args:
        response_text (str): Raw response text from the target.
        request (MessagePiece): Original request piece that caused the error.
        is_content_filter (bool): Whether the response is known to be content-filtered.
        error_code (int): Status code to include in the generated error payload.

    Returns:
        Message: A constructed error response message.

    Raises:
        RuntimeError: If the response does not match bad-request content-filter conditions.

    """
    if is_content_filter or any(marker in response_text for marker in CONTENT_FILTER_MARKERS):
        # Handle bad request error when content filter system detects harmful content
        bad_request_exception = BadRequestException(status_code=error_code, message=response_text)
        resp_text = bad_request_exception.process_exception()
        response_entry = construct_response_from_request(
            request=request, response_text_pieces=[resp_text], response_type="error", error="blocked"
        )
    else:
        raise  # noqa: PLE0704

    return response_entry
