# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import os
from abc import ABC
from typing import Any, Callable, Optional

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
    """Get the maximum number of retry attempts for custom result retry decorator."""
    return int(os.getenv("CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS", 10))


def get_retry_max_num_attempts() -> int:
    """Get the maximum number of retry attempts."""
    return int(os.getenv("RETRY_MAX_NUM_ATTEMPTS", 10))


def _get_retry_wait_min_seconds() -> int:
    """Get the minimum wait time in seconds between retries."""
    return int(os.getenv("RETRY_WAIT_MIN_SECONDS", 5))


def _get_retry_wait_max_seconds() -> int:
    """Get the maximum wait time in seconds between retries."""
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


class PyritException(Exception, ABC):
    def __init__(self, *, status_code: int = 500, message: str = "An error occurred") -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Status Code: {status_code}, Message: {message}")

    def process_exception(self) -> str:
        """
        Logs and returns a string representation of the exception.
        """
        log_message = f"{self.__class__.__name__} encountered: Status Code: {self.status_code}, Message: {self.message}"
        logger.error(log_message)
        # Return a string representation of the exception so users can extract and parse
        return json.dumps({"status_code": self.status_code, "message": self.message})


class BadRequestException(PyritException):
    """Exception class for bad client requests."""

    def __init__(self, *, status_code: int = 400, message: str = "Bad Request") -> None:
        super().__init__(status_code=status_code, message=message)


class RateLimitException(PyritException):
    """Exception class for authentication errors."""

    def __init__(self, *, status_code: int = 429, message: str = "Rate Limit Exception") -> None:
        super().__init__(status_code=status_code, message=message)


class ServerErrorException(PyritException):
    """Exception class for opaque 5xx errors returned by the server."""

    def __init__(self, *, status_code: int = 500, message: str = "Server Error", body: Optional[str] = None) -> None:
        super().__init__(status_code=status_code, message=message)
        self.body = body


class EmptyResponseException(BadRequestException):
    """Exception class for empty response errors."""

    def __init__(self, *, status_code: int = 204, message: str = "No Content") -> None:
        super().__init__(status_code=status_code, message=message)


class InvalidJsonException(PyritException):
    """Exception class for blocked content errors."""

    def __init__(self, *, message: str = "Invalid JSON Response") -> None:
        super().__init__(message=message)


class MissingPromptPlaceholderException(PyritException):
    """Exception class for missing prompt placeholder errors."""

    def __init__(self, *, message: str = "No prompt placeholder") -> None:
        super().__init__(message=message)


def pyrit_custom_result_retry(
    retry_function: Callable[..., bool], retry_max_num_attempts: Optional[int] = None
) -> Callable[..., Any]:
    """
    A decorator to apply retry logic with exponential backoff to a function.

    Retries the function if the result of the retry_function is True,
    with a wait time between retries that follows an exponential backoff strategy.
    Logs retry attempts at the INFO level and stops after a maximum number of attempts.

    Args:
        retry_function (Callable): The boolean function to determine if a retry should occur based
            on the result of the decorated function.
        retry_max_num_attempts (Optional, int): The maximum number of retry attempts. Defaults to
            environment variable CUSTOM_RESULT_RETRY_MAX_NUM_ATTEMPTS or 10.
        func (Callable): The function to be decorated.

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
    A decorator to apply retry logic with exponential backoff to a function.

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
    A decorator to apply retry logic to a function.

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
    A decorator to apply retry logic.

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


def handle_bad_request_exception(
    response_text: str,
    request: MessagePiece,
    is_content_filter: bool = False,
    error_code: int = 400,
) -> Message:
    if (
        "content_filter" in response_text
        or "Invalid prompt: your prompt was flagged as potentially violating our usage policy." in response_text
        or is_content_filter
    ):
        # Handle bad request error when content filter system detects harmful content
        bad_request_exception = BadRequestException(status_code=error_code, message=response_text)
        resp_text = bad_request_exception.process_exception()
        response_entry = construct_response_from_request(
            request=request, response_text_pieces=[resp_text], response_type="error", error="blocked"
        )
    else:
        raise

    return response_entry
