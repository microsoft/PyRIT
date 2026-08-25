# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from pyrit.exceptions import PyritException
from pyrit.models import (
    TOKEN_USAGE_METADATA_PREFIX,
    Message,
    MessagePiece,
    TokenUsage,
    construct_response_from_request,
)

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

    @wraps(func)
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


#: ``prompt_metadata`` keys that record why a provider stopped generating. Every API shape names this
#: differently, so the union is reserved rather than any single key: a target that captures response
#: metadata clears all of them and writes back only what its own provider reported. A path that never
#: reaches a provider response, such as the one that handles an HTTP 400, captures nothing and so
#: clears nothing.
RESERVED_RESPONSE_METADATA_KEYS: frozenset[str] = frozenset({"finish_reason", "status", "incomplete_reason"})


def set_response_metadata(
    *,
    pieces: list[MessagePiece],
    finish_reason: Any = None,
    status: Any = None,
    incomplete_reason: Any = None,
) -> None:
    """
    Record provider-reported, response-level metadata on the first response piece.

    ``prompt_metadata`` is caller-controlled, and ``construct_response_from_request`` merges the
    request's entries into every response piece, so a caller-supplied value could otherwise be
    mistaken for the provider's. ``RESERVED_RESPONSE_METADATA_KEYS`` are therefore reserved for the
    provider: all of them are cleared from every piece first, then the reported ones are set on the
    first piece. Clearing the whole set in one pass — rather than one key per call — is what makes
    the reservation hold, since a target only writes the subset its own API reports. Response-level
    metadata lives on the first piece, matching where ``capture_token_usage`` writes token counts.

    There is one keyword parameter per reserved key so the two cannot drift apart: a key the
    clearing loop does not know about is a type error rather than a value that is silently
    persisted. ``prompt_metadata`` is persisted as JSON, so anything that is not a non-empty
    string — including a missing field read off a loosely-typed response object — is treated as
    "not reported" and leaves that key unset.

    Args:
        pieces (list[MessagePiece]): The constructed response pieces.
        finish_reason (Any): The stop reason a Chat Completions or Completions response reported.
        status (Any): The status a Responses API response reported.
        incomplete_reason (Any): The incomplete detail a Responses API response reported.
    """
    if not pieces:
        return

    values = {"finish_reason": finish_reason, "status": status, "incomplete_reason": incomplete_reason}

    for piece in pieces:
        for reserved_key in RESERVED_RESPONSE_METADATA_KEYS:
            piece.prompt_metadata.pop(reserved_key, None)

    for key, value in values.items():
        if isinstance(value, str) and value:
            pieces[0].prompt_metadata[key] = value


def set_token_usage_metadata(*, pieces: list[MessagePiece], usage: TokenUsage | None) -> None:
    """
    Record the provider's token counts on the first response piece.

    The whole ``token_usage_`` prefix is reserved for the provider for the same reason
    ``RESERVED_RESPONSE_METADATA_KEYS`` are, with one extra consequence: the public
    ``TokenUsage.from_metadata`` would read a caller-supplied count back as if the API had reported
    it. Clearing the prefix is what makes "no usage reported" distinguishable from "the caller
    guessed", which matters most on the paths that carry no usage at all, such as a content-filtered
    response.

    Args:
        pieces (list[MessagePiece]): The constructed response pieces.
        usage (TokenUsage | None): The provider's parsed counts, or None when the response reported
            no usage. Either way the stale keys are cleared first; only reported counts are written.
    """
    if not pieces:
        return

    for piece in pieces:
        for key in [k for k in piece.prompt_metadata if k.startswith(TOKEN_USAGE_METADATA_PREFIX)]:
            del piece.prompt_metadata[key]

    if usage is not None:
        pieces[0].prompt_metadata.update(usage.to_metadata())


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
