# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Helpers for parsing responses in the OpenAI *Chat Completions* wire format.

Both the OpenAI SDK and LiteLLM return responses with the same shape
(``response.choices[0].message`` with ``content`` / ``tool_calls``, plus ``response.usage``),
so validation, piece construction, token-usage capture, and content-filter handling can be
shared across every target that speaks that format.
"""

import base64
import json
import logging
from typing import Any

from openai.types.chat import ChatCompletion

from pyrit.exceptions import (
    EmptyResponseException,
    PyritException,
    handle_bad_request_exception,
)
from pyrit.memory import data_serializer_factory
from pyrit.models import (
    Message,
    MessagePiece,
    TokenUsage,
    construct_response_from_request,
    read_usage_int,
    read_usage_value,
)
from pyrit.prompt_target.common.utils import set_response_metadata, set_token_usage_metadata

logger = logging.getLogger(__name__)

# Finish reasons that represent a well-formed (non-error) completion. ``content_filter`` is
# included because it is handled separately (see ``is_content_filter_response``) before
# validation; a target should check for content filtering first.
DEFAULT_VALID_FINISH_REASONS: frozenset[str] = frozenset({"stop", "length", "content_filter", "tool_calls"})


def get_finish_reason(*, response: ChatCompletion) -> str | None:
    """
    Extract the first choice's ``finish_reason`` from a Chat Completions response.

    Args:
        response (ChatCompletion): The Chat Completions response object.

    Returns:
        str | None: The first choice's ``finish_reason``, or None when there are no choices.
    """
    if not response.choices:
        return None
    return response.choices[0].finish_reason


def detect_response_content(message: Any) -> tuple[bool, bool, bool]:
    """
    Detect which content types are present in a Chat Completions ``message``.

    Args:
        message (Any): The ``response.choices[0].message`` object.

    Returns:
        tuple[bool, bool, bool]: ``(has_content, has_audio, has_tool_calls)``. Structured
            refusals count as content because they produce a valid blocked response.
    """
    refusal = getattr(message, "refusal", None)
    has_content = bool(getattr(message, "content", None)) or (isinstance(refusal, str) and bool(refusal))
    has_audio = getattr(message, "audio", None) is not None
    has_tool_calls = bool(getattr(message, "tool_calls", None))
    return has_content, has_audio, has_tool_calls


def validate_chat_completion_response(
    *,
    response: Any,
    valid_finish_reasons: frozenset[str] = DEFAULT_VALID_FINISH_REASONS,
) -> None:
    """
    Validate an OpenAI-compatible Chat Completions response.

    Checks for missing choices, an unexpected ``finish_reason``, and at least one usable
    response type (text content, audio, or tool calls). Content-filter responses should be
    handled by the caller *before* calling this (via ``is_content_filter_response``).

    Args:
        response (Any): The Chat Completions response object.
        valid_finish_reasons (frozenset[str]): Accepted ``finish_reason`` values.

    Raises:
        PyritException: If there are no choices or the ``finish_reason`` is unexpected.
        EmptyResponseException: If the response has no content, audio, or tool calls.
    """
    if not getattr(response, "choices", None):
        raise PyritException(message="No choices returned in the completion response.")

    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason not in valid_finish_reasons:
        detail = response.model_dump_json() if hasattr(response, "model_dump_json") else str(response)
        raise PyritException(message=f"Unknown finish_reason {finish_reason} from response: {detail}")

    has_content, has_audio, has_tool_calls = detect_response_content(getattr(choice, "message", None))
    if not (has_content or has_audio or has_tool_calls):
        logger.error("The chat returned an empty response (no content, audio, or tool_calls).")
        raise EmptyResponseException(message="The chat returned an empty response (no content, audio, or tool_calls).")


def _build_text_piece(*, content: str, request: MessagePiece) -> MessagePiece:
    """
    Build a single text response piece.

    Args:
        content (str): The text content.
        request (MessagePiece): The originating request piece.

    Returns:
        MessagePiece: The constructed text piece.
    """
    return construct_response_from_request(
        request=request,
        response_text_pieces=[content],
        response_type="text",
    ).message_pieces[0]


def _build_tool_pieces(*, message: Any, request: MessagePiece) -> list[MessagePiece]:
    """
    Build function_call response pieces from a message's ``tool_calls``.

    Args:
        message (Any): The ``response.choices[0].message`` object.
        request (MessagePiece): The originating request piece.

    Returns:
        list[MessagePiece]: The constructed function_call pieces (may be empty).
    """
    pieces: list[MessagePiece] = []
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return pieces

    for tool_call in tool_calls:
        tool_call_data = {
            "type": "function",
            "id": tool_call.id,
            "function": {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
            },
        }
        pieces.append(
            construct_response_from_request(
                request=request,
                response_text_pieces=[json.dumps(tool_call_data)],
                response_type="function_call",
            ).message_pieces[0]
        )
    return pieces


async def save_audio_response_async(*, audio_data_base64: str, audio_format: str = "wav") -> str:
    """
    Decode and persist base64 audio from a Chat Completions response to a file.

    Args:
        audio_data_base64 (str): Base64-encoded audio data from ``message.audio.data``.
        audio_format (str): The audio format (e.g. ``"wav"``, ``"mp3"``, ``"pcm16"``). Raw
            ``pcm16`` is wrapped in a WAV container (24kHz mono).

    Returns:
        str: The file path where the audio was saved.
    """
    audio_bytes = base64.b64decode(audio_data_base64)
    extension = f".{audio_format}" if audio_format != "pcm16" else ".wav"

    audio_serializer = data_serializer_factory(
        category="prompt-memory-entries",
        data_type="audio_path",
        extension=extension,
    )

    if audio_format == "pcm16":
        # Raw PCM needs WAV headers - OpenAI uses 24kHz mono PCM16
        await audio_serializer.save_formatted_audio_async(
            data=audio_bytes,
            num_channels=1,
            sample_width=2,
            sample_rate=24000,
        )
    else:
        await audio_serializer.save_data_async(audio_bytes)

    return audio_serializer.value


async def _build_audio_pieces_async(
    *, message: Any, request: MessagePiece, audio_format: str = "wav"
) -> list[MessagePiece]:
    """
    Build response pieces for an audio message: a transcript text piece and a saved audio file.

    Args:
        message (Any): The ``response.choices[0].message`` object.
        request (MessagePiece): The originating request piece.
        audio_format (str): The audio format used to persist the audio data.

    Returns:
        list[MessagePiece]: The transcript and/or ``audio_path`` pieces (may be empty).
    """
    pieces: list[MessagePiece] = []
    audio_response = getattr(message, "audio", None)
    if audio_response is None:
        return pieces

    transcript = getattr(audio_response, "transcript", None)
    if transcript:
        pieces.append(
            construct_response_from_request(
                request=request,
                response_text_pieces=[transcript],
                response_type="text",
                prompt_metadata={"transcription": "audio"},
            ).message_pieces[0]
        )

    audio_data = getattr(audio_response, "data", None)
    if audio_data:
        audio_path = await save_audio_response_async(audio_data_base64=audio_data, audio_format=audio_format)
        pieces.append(
            construct_response_from_request(
                request=request,
                response_text_pieces=[audio_path],
                response_type="audio_path",
            ).message_pieces[0]
        )

    return pieces


async def build_response_pieces_async(
    *, response: Any, request: MessagePiece, audio_format: str = "wav"
) -> list[MessagePiece]:
    """
    Build all response pieces (refusal, text, audio, and tool calls) from a Chat Completions response.

    A structured refusal produces one blocked error piece. Otherwise, pieces are ordered text,
    then audio (transcript + file), then tool calls.

    Args:
        response (Any): The Chat Completions response object.
        request (MessagePiece): The originating request piece.
        audio_format (str): The audio format used to persist any audio data.

    Returns:
        list[MessagePiece]: All constructed response pieces (may be empty).
    """
    message = response.choices[0].message
    pieces: list[MessagePiece] = []

    content = getattr(message, "content", None)
    refusal = getattr(message, "refusal", None)
    if isinstance(refusal, str) and refusal:
        partial_content = content if isinstance(content, str) and content else None
        refusal_message = build_content_filter_message(
            response=refusal,
            request=request,
            partial_content=partial_content,
        )
        for piece in refusal_message.message_pieces:
            piece.mark_as_structured_refusal(refusal=refusal)
        return refusal_message.message_pieces

    if content:
        pieces.append(_build_text_piece(content=content, request=request))

    pieces.extend(await _build_audio_pieces_async(message=message, request=request, audio_format=audio_format))
    pieces.extend(_build_tool_pieces(message=message, request=request))
    return pieces


def capture_token_usage(*, pieces: list[MessagePiece], response: Any) -> None:
    """
    Copy token-usage numbers from ``response.usage`` into the first piece's metadata.

    Parses the Chat Completions ``usage`` payload (see ``token_usage_from_chat_completion``) and
    writes the resulting counts onto the first piece via ``set_token_usage_metadata``, which also
    clears any stale caller-supplied counts. Only fields the provider actually reports are written;
    missing counts are omitted rather than stored as a misleading zero.

    Args:
        pieces (list[MessagePiece]): The constructed response pieces.
        response (Any): The Chat Completions response object.
    """
    usage = getattr(response, "usage", None)
    set_token_usage_metadata(pieces=pieces, usage=token_usage_from_chat_completion(usage) if usage else None)


def capture_usage_and_finish_reason(*, pieces: list[MessagePiece], response: Any) -> None:
    """
    Copy the provider's response metadata into the response pieces' metadata.

    Captures token usage (see ``capture_token_usage``) together with the first choice's
    ``finish_reason``, which records why generation stopped — ``stop``, ``length`` (token limit),
    ``content_filter`` or ``tool_calls``. Both come from the same response object, so capturing them
    together keeps every call site consistent: success, truncation, and content filter all record
    the same set of keys. Applies to every ``usage``-plus-``choices`` response shape: Chat
    Completions, Completions, and the LiteLLM responses modeled on them.

    Args:
        pieces (list[MessagePiece]): The constructed response pieces.
        response (Any): The response object. Objects that carry no ``usage`` or no ``choices`` —
            such as the synthetic response used when the SDK raises on a content filter — are
            tolerated and simply yield no metadata.
    """
    if not pieces:
        return

    capture_token_usage(pieces=pieces, response=response)

    # Synthetic content-filter responses have no ``choices`` attribute at all, so probe for it
    # rather than relying on ``get_finish_reason``'s empty-list guard.
    choices = getattr(response, "choices", None)
    finish_reason = get_finish_reason(response=response) if choices else None
    set_response_metadata(pieces=pieces, finish_reason=finish_reason)


def token_usage_from_chat_completion(usage: Any) -> TokenUsage:
    """
    Build a ``TokenUsage`` from a Chat Completions ``usage`` payload (OpenAI or LiteLLM).

    Reads the top-level ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` counts and the
    nested ``prompt_tokens_details`` / ``completion_tokens_details`` breakdowns, deriving
    ``total_tokens`` when the provider omits it. Non-OpenAI providers routed through LiteLLM
    (for example, Anthropic) surface prompt-cache tokens at the top level rather than inside the
    details object, so ``cache_read_input_tokens`` / ``cache_creation_input_tokens`` are picked up
    as well. Unmodeled detail counts (audio, predicted-output) ride along in ``extra``.

    This parser is specific to the Chat Completions wire format. The Responses API reports usage
    under different names (``input_tokens`` / ``output_tokens``); a target that speaks that format
    should parse it in its own module rather than overloading this function, reusing the shared
    ``read_usage_value`` / ``read_usage_int`` reads.

    Args:
        usage (Any): The Chat Completions usage object (attribute object or mapping).

    Returns:
        TokenUsage: The parsed token usage.
    """
    input_tokens = read_usage_int(source=usage, name="prompt_tokens")
    output_tokens = read_usage_int(source=usage, name="completion_tokens")
    total_tokens = read_usage_int(source=usage, name="total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    prompt_details = read_usage_value(source=usage, name="prompt_tokens_details")
    completion_details = read_usage_value(source=usage, name="completion_tokens_details")

    cached_tokens = read_usage_int(source=prompt_details, name="cached_tokens")
    if cached_tokens is None:
        cached_tokens = read_usage_int(source=usage, name="cache_read_input_tokens")
    reasoning_tokens = read_usage_int(source=completion_details, name="reasoning_tokens")

    extra: dict[str, int] = {}
    _add_extra(extra, "input_audio_tokens", read_usage_int(source=prompt_details, name="audio_tokens"))
    _add_extra(extra, "cache_write_tokens", read_usage_int(source=usage, name="cache_creation_input_tokens"))
    _add_extra(extra, "output_audio_tokens", read_usage_int(source=completion_details, name="audio_tokens"))
    _add_extra(
        extra,
        "accepted_prediction_tokens",
        read_usage_int(source=completion_details, name="accepted_prediction_tokens"),
    )
    _add_extra(
        extra,
        "rejected_prediction_tokens",
        read_usage_int(source=completion_details, name="rejected_prediction_tokens"),
    )

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        extra=extra,
    )


def _add_extra(target: dict[str, int], name: str, value: int | None) -> None:
    """
    Insert ``name``->``value`` into ``target`` only when ``value`` is not None.

    Args:
        target (dict[str, int]): The destination mapping.
        name (str): The key to set.
        value (int | None): The value to store, ignored when None.
    """
    if value is not None:
        target[name] = value


def is_content_filter_response(response: Any) -> bool:
    """
    Return whether a Chat Completions response was blocked by a content filter.

    Args:
        response (Any): The Chat Completions response object.

    Returns:
        bool: True if ``finish_reason == "content_filter"``, False otherwise.
    """
    try:
        return bool(response.choices) and bool(response.choices[0].finish_reason == "content_filter")
    except (AttributeError, IndexError):
        return False


def extract_partial_content(response: Any) -> str | None:
    """
    Extract any partial text the model produced before a content filter triggered.

    Args:
        response (Any): The Chat Completions response object.

    Returns:
        str | None: The partial text, or None if none was generated.
    """
    try:
        content = response.choices[0].message.content
        if isinstance(content, str) and content:
            return content
    except (AttributeError, IndexError):
        pass
    return None


def build_content_filter_message(
    *,
    response: Any,
    request: MessagePiece,
    partial_content: str | None = None,
) -> Message:
    """
    Build an ``error``-type Message for a content-filtered response.

    Rather than raising, blocked responses are surfaced as an error Message so attacks can
    continue. When ``partial_content`` is available it is attached to each piece as
    ``prompt_metadata["partial_content"]`` so scorers can still evaluate what the model
    produced before the block.

    Args:
        response (Any): The Chat Completions response object (or an object exposing
            ``model_dump_json``) describing the block.
        request (MessagePiece): The originating request piece.
        partial_content (str | None): Any partial model output recovered before the block.

    Returns:
        Message: The constructed error Message with ``error="blocked"``.
    """
    response_text = response.model_dump_json() if hasattr(response, "model_dump_json") else str(response)
    error_message = handle_bad_request_exception(
        response_text=response_text,
        request=request,
        error_code=200,
        is_content_filter=True,
    )

    if partial_content:
        for piece in error_message.message_pieces:
            piece.prompt_metadata["partial_content"] = partial_content

    return error_message
