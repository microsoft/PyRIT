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

from pyrit.exceptions import (
    EmptyResponseException,
    PyritException,
    handle_bad_request_exception,
)
from pyrit.models import (
    Message,
    MessagePiece,
    TokenUsage,
    construct_response_from_request,
    data_serializer_factory,
)

logger = logging.getLogger(__name__)

# Finish reasons that represent a well-formed (non-error) completion. ``content_filter`` is
# included because it is handled separately (see ``is_content_filter_response``) before
# validation; a target should check for content filtering first.
DEFAULT_VALID_FINISH_REASONS: frozenset[str] = frozenset({"stop", "length", "content_filter", "tool_calls"})


def detect_response_content(message: Any) -> tuple[bool, bool, bool]:
    """
    Detect which content types are present in a Chat Completions ``message``.

    Args:
        message (Any): The ``response.choices[0].message`` object.

    Returns:
        tuple[bool, bool, bool]: ``(has_content, has_audio, has_tool_calls)``.
    """
    has_content = bool(getattr(message, "content", None))
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


def build_text_and_tool_pieces(*, response: Any, request: MessagePiece) -> list[MessagePiece]:
    """
    Build response pieces for text content and tool/function calls.

    Audio and other modality-specific parsing is intentionally left to the caller so that
    provider-specific handling (e.g. saving audio files) stays out of this shared helper.

    Args:
        response (Any): The Chat Completions response object.
        request (MessagePiece): The originating request piece (for lineage propagation).

    Returns:
        list[MessagePiece]: Text and function_call pieces (may be empty).
    """
    message = response.choices[0].message
    pieces: list[MessagePiece] = []

    content = getattr(message, "content", None)
    if content:
        pieces.append(_build_text_piece(content=content, request=request))

    pieces.extend(_build_tool_pieces(message=message, request=request))
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
        await audio_serializer.save_formatted_audio(
            data=audio_bytes,
            num_channels=1,
            sample_width=2,
            sample_rate=24000,
        )
    else:
        await audio_serializer.save_data(audio_bytes)

    return audio_serializer.value


async def build_audio_pieces_async(
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
    Build all response pieces (text, audio, and tool calls) from a Chat Completions response.

    Pieces are ordered text, then audio (transcript + file), then tool calls.

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
    if content:
        pieces.append(_build_text_piece(content=content, request=request))

    pieces.extend(await build_audio_pieces_async(message=message, request=request, audio_format=audio_format))
    pieces.extend(_build_tool_pieces(message=message, request=request))
    return pieces


def capture_token_usage(*, pieces: list[MessagePiece], response: Any) -> None:
    """
    Copy token-usage numbers from ``response.usage`` into the first piece's metadata.

    Parses the provider usage through :class:`~pyrit.models.TokenUsage`, which reads both the
    top-level counts and the nested ``prompt_tokens_details`` / ``completion_tokens_details``
    breakdowns (so cached and reasoning tokens are captured, not just prompt/completion/total).
    Only fields the provider actually reports are written; missing counts are omitted rather than
    stored as a misleading zero. No-op when the response has no usage data or there are no pieces.

    Args:
        pieces (list[MessagePiece]): The constructed response pieces.
        response (Any): The Chat Completions response object.
    """
    usage = getattr(response, "usage", None)
    if not usage or not pieces:
        return

    token_usage = TokenUsage.from_provider_usage(usage)
    pieces[0].prompt_metadata.update(token_usage.to_metadata())


def is_content_filter_response(response: Any) -> bool:
    """
    Return whether a Chat Completions response was blocked by a content filter.

    Args:
        response (Any): The Chat Completions response object.

    Returns:
        bool: True if ``finish_reason == "content_filter"``, False otherwise.
    """
    try:
        return bool(response.choices) and response.choices[0].finish_reason == "content_filter"
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
        choice = response.choices[0]
        if choice.message and choice.message.content:
            return choice.message.content
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
    ``prompt_metadata["partial_content"]`` so scorers with ``score_blocked_content=True`` can
    still evaluate what the model produced.

    Args:
        response (Any): The Chat Completions response object (or an object exposing
            ``model_dump_json``) describing the block.
        request (MessagePiece): The originating request piece.
        partial_content (str | None): Any partial model output recovered before the block.

    Returns:
        Message: The constructed error Message with ``error="blocked"``.
    """
    logger.warning("Output content filtered by content policy.")

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
