# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from pyrit.exceptions import PyritException
from pyrit.models import Message, MessagePiece, construct_response_from_request

logger = logging.getLogger(__name__)


def validate_temperature(temperature: float | None) -> None:
    """
    Validate that temperature parameter is within valid range.

    Args:
        temperature: The temperature value to validate (0-2 inclusive).

    Raises:
        PyritException: If temperature is not between 0 and 2 (inclusive).
    """
    if temperature is not None and (temperature < 0 or temperature > 2):
        raise PyritException(message="temperature must be between 0 and 2 (inclusive).")


def validate_top_p(top_p: float | None) -> None:
    """
    Validate that top_p parameter is within valid range.

    Args:
        top_p: The top_p value to validate (0-1 inclusive).

    Raises:
        PyritException: If top_p is not between 0 and 1 (inclusive).
    """
    if top_p is not None and (top_p < 0 or top_p > 1):
        raise PyritException(message="top_p must be between 0 and 1 (inclusive).")


def limit_requests_per_minute(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Enforce rate limit of the target through setting requests per minute.
    This should be applied to all send_prompt_async() functions on PromptTarget.

    Args:
        func (Callable): The function to be decorated.

    Returns:
        Callable: The decorated function with a sleep introduced.
    """

    async def set_max_rpm_async(*args: Any, **kwargs: Any) -> Any:
        self = args[0]
        rpm = getattr(self, "_max_requests_per_minute", None)
        if rpm and rpm > 0:
            await asyncio.sleep(60 / rpm)

        return await func(*args, **kwargs)

    return set_max_rpm_async


def build_empty_truncated_response(*, request: MessagePiece) -> Message:
    """
    Build a graceful empty response for a token-limit-truncated model response.

    A response truncated at the token limit (Chat Completions ``finish_reason == "length"`` or the
    Responses API ``status == "incomplete"`` with ``reason == "max_output_tokens"``) may legitimately
    contain no visible content. Callers gate this on their own truncation check (for example a
    target's ``_is_truncated_response``); returning an empty ``error="empty"`` text response lets the
    run continue instead of raising.

    Args:
        request (MessagePiece): The originating request piece.

    Returns:
        Message: An empty text response marked with ``error="empty"``.
    """
    return construct_response_from_request(
        request=request,
        response_text_pieces=[""],
        response_type="text",
        error="empty",
    )


def warn_truncated_response(*, signal: str, limit_parameter: str) -> None:
    """
    Log the shared warning for a response cut off at the output-token limit.

    Every API shape signals truncation differently but the advice is identical, so the wording
    lives here to keep targets from drifting apart.

    Args:
        signal (str): How the API reported the truncation, quoted into the message (for example
            ``"finish_reason='length'"``).
        limit_parameter (str): The request parameter to raise (for example ``"max_output_tokens"``).
    """
    logger.warning(
        f"The response was truncated because it reached the token limit ({signal}). Reasoning models "
        f"consume tokens on hidden reasoning in addition to the visible answer, so a low "
        f"{limit_parameter} can truncate or empty the response. Increase {limit_parameter} if you "
        "expected complete content."
    )
