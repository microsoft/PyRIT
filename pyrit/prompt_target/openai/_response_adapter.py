# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Any, Generic, TypeVar

from openai.types.chat import ChatCompletion
from openai.types.responses import Response, ResponseOutputText

from pyrit.exceptions import EmptyResponseException, PyritException
from pyrit.models import MessagePiece, TokenUsage, read_usage_int, read_usage_value
from pyrit.prompt_target.common.chat_completions_response_parser import (
    capture_token_usage,
    capture_usage_and_finish_reason,
    extract_partial_content,
    get_finish_reason,
    is_content_filter_response,
    validate_chat_completion_response,
)
from pyrit.prompt_target.common.utils import (
    set_response_metadata,
    set_token_usage_metadata,
    warn_truncated_response,
)
from pyrit.prompt_target.openai.openai_error_handling import _is_content_filter_error

logger = logging.getLogger(__name__)

ResponseT = TypeVar("ResponseT", contravariant=True)


class OpenAIResponseAdapter(Generic[ResponseT]):
    """Base response-format behavior used by ``OpenAITarget``."""

    def is_content_filtered(self, *, response: ResponseT) -> bool:
        """Return whether the response was blocked by a content filter."""
        return False

    def extract_partial_content(self, *, response: ResponseT) -> str | None:
        """
        Extract content emitted before a content filter stopped generation.

        Args:
            response (ResponseT): The provider response.

        Returns:
            str | None: Partial content, if available.
        """
        return None

    def capture_metadata(self, *, response: ResponseT, pieces: list[MessagePiece]) -> None:
        """Copy provider response metadata to PyRIT response pieces."""

    def validate(self, *, response: ResponseT, is_truncated: bool) -> None:
        """Validate the provider response."""

    def is_truncated(self, *, response: ResponseT) -> bool:
        """Return whether generation stopped at the output-token limit."""
        return False


class ChatCompletionsResponseAdapter(OpenAIResponseAdapter[ChatCompletion]):
    """Response behavior for the OpenAI Chat Completions wire format."""

    def is_content_filtered(self, *, response: ChatCompletion) -> bool:
        """Return whether ``finish_reason`` reports a content filter."""
        return is_content_filter_response(response)

    def extract_partial_content(self, *, response: ChatCompletion) -> str | None:
        """
        Extract text emitted before a content filter stopped generation.

        Args:
            response (ChatCompletion): The provider response.

        Returns:
            str | None: Partial text, if present.
        """
        return extract_partial_content(response)

    def capture_metadata(self, *, response: ChatCompletion, pieces: list[MessagePiece]) -> None:
        """Capture token usage and the first choice's finish reason."""
        capture_usage_and_finish_reason(pieces=pieces, response=response)

    def validate(self, *, response: ChatCompletion, is_truncated: bool) -> None:
        """Validate the response while accepting token-limit truncation."""
        if is_truncated:
            warn_truncated_response(signal="finish_reason='length'", limit_parameter="max_completion_tokens")
            return
        validate_chat_completion_response(response=response)

    def is_truncated(self, *, response: ChatCompletion) -> bool:
        """Return whether ``finish_reason`` reports token-limit truncation."""
        return get_finish_reason(response=response) == "length"


class CompletionsResponseAdapter(OpenAIResponseAdapter[Any]):
    """Response behavior for the legacy OpenAI Completions wire format."""

    def capture_metadata(self, *, response: Any, pieces: list[MessagePiece]) -> None:
        """Capture call-level usage and each choice's finish reason."""
        capture_token_usage(pieces=pieces, response=response)

        choices = getattr(response, "choices", None) or []
        for index, piece in enumerate(pieces):
            choice = choices[index] if index < len(choices) else None
            set_response_metadata(pieces=[piece], finish_reason=getattr(choice, "finish_reason", None))


class ResponsesResponseAdapter(OpenAIResponseAdapter[Response]):
    """Response behavior for the OpenAI Responses API wire format."""

    def is_content_filtered(self, *, response: Response) -> bool:
        """Return whether the response reports content filtering."""
        error = getattr(response, "error", None)
        if error is not None and _is_content_filter_error(response.model_dump()):
            return True

        if getattr(response, "status", None) != "incomplete":
            return False
        incomplete_details = getattr(response, "incomplete_details", None)
        return incomplete_details is not None and bool(incomplete_details.reason == "content_filter")

    def extract_partial_content(self, *, response: Response) -> str | None:
        """
        Extract text from completed message sections in a filtered response.

        Args:
            response (Response): The provider response.

        Returns:
            str | None: Partial text, if present.
        """
        try:
            parts: list[str] = []
            for section in response.output or []:
                if getattr(section, "type", None) != "message" or getattr(section, "status", None) != "completed":
                    continue
                parts.extend(
                    content_item.text
                    for content_item in getattr(section, "content", None) or []
                    if isinstance(content_item, ResponseOutputText)
                    and isinstance(content_item.text, str)
                    and content_item.text
                )
        except (AttributeError, IndexError, TypeError):
            return None
        return "\n".join(parts) if parts else None

    def capture_metadata(self, *, response: Response, pieces: list[MessagePiece]) -> None:
        """Capture token usage, response status, and incomplete reason."""
        if not pieces:
            return

        usage = getattr(response, "usage", None)
        parsed_usage = token_usage_from_responses(usage) if usage is not None else None
        set_token_usage_metadata(pieces=pieces, usage=parsed_usage)

        status = getattr(response, "status", None)
        incomplete_details = getattr(response, "incomplete_details", None)
        incomplete_reason = getattr(incomplete_details, "reason", None) if incomplete_details else None
        set_response_metadata(pieces=pieces, status=status, incomplete_reason=incomplete_reason)

    def validate(self, *, response: Response, is_truncated: bool) -> None:
        """
        Validate the response while accepting token-limit truncation.

        Args:
            response (Response): The provider response.
            is_truncated (bool): Whether the target classified the response as token-limit truncation.

        Raises:
            PyritException: If the provider reports an error or unexpected status.
            EmptyResponseException: If a completed response has no output.
        """
        if response.error is not None and response.error.code != "content_filter":
            raise PyritException(message=f"Response error: {response.error.code} - {response.error.message}")

        if is_truncated:
            warn_truncated_response(
                signal="status='incomplete', reason='max_output_tokens'",
                limit_parameter="max_output_tokens",
            )
            return

        if response.status != "completed":
            raise PyritException(message=f"Unexpected status: {response.status}")

        if not response.output:
            logger.error("The response returned no valid output.")
            raise EmptyResponseException(message="The response returned an empty response.")

    def is_truncated(self, *, response: Response) -> bool:
        """Return whether the response stopped at ``max_output_tokens``."""
        if response.status != "incomplete":
            return False
        incomplete_details = response.incomplete_details
        reason = incomplete_details.reason if incomplete_details else None
        return reason == "max_output_tokens"


def token_usage_from_responses(usage: Any) -> TokenUsage:
    """
    Build a ``TokenUsage`` from a Responses API ``usage`` payload.

    Args:
        usage (Any): The Responses API usage object.

    Returns:
        TokenUsage: The parsed token usage.
    """
    input_details = read_usage_value(source=usage, name="input_tokens_details")
    output_details = read_usage_value(source=usage, name="output_tokens_details")

    input_tokens = read_usage_int(source=usage, name="input_tokens")
    output_tokens = read_usage_int(source=usage, name="output_tokens")
    total_tokens = read_usage_int(source=usage, name="total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    extra: dict[str, int] = {}
    cache_write_tokens = read_usage_int(source=input_details, name="cache_write_tokens")
    if cache_write_tokens is not None:
        extra["cache_write_tokens"] = cache_write_tokens

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=read_usage_int(source=output_details, name="reasoning_tokens"),
        cached_tokens=read_usage_int(source=input_details, name="cached_tokens"),
        extra=extra,
    )
