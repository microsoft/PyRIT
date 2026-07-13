# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Helpers for building request payloads in the OpenAI *Chat Completions* wire format.

This format (a ``messages`` list of ``{"role", "content"}`` dicts, where ``content`` is
either a string or a list of typed content parts) is an industry standard implemented by
the OpenAI SDK and by LiteLLM's ``acompletion``. Keeping these builders here lets every
target that speaks that format (``OpenAIChatTarget``, ``LiteLLMChatTarget``, ...) share one
implementation instead of re-inventing message construction.
"""

from collections.abc import MutableSequence
from typing import Any

from pyrit.memory import DataTypeSerializer, data_serializer_factory
from pyrit.memory.storage import convert_local_image_to_data_url_async
from pyrit.models import (
    ChatMessage,
    Message,
    MessagePiece,
)
from pyrit.prompt_target.common.json_response_config import _JsonResponseConfig

# Data types that render as a plain text content part.
_TEXT_DATA_TYPES = ("text", "error")

# Audio formats accepted by the OpenAI Chat Completions ``input_audio`` content part.
# OpenAI SDK: openai/types/chat/chat_completion_content_part_input_audio_param.py
# defines format: Required[Literal["wav", "mp3"]].
_INPUT_AUDIO_EXTENSIONS = (".wav", ".mp3")


def is_text_only_conversation(conversation: MutableSequence[Message]) -> bool:
    """
    Return whether every message in the conversation is a single text (or error) piece.

    When true, the simpler text-only message format can be used, which is more broadly
    compatible with OpenAI-compatible providers that don't accept multipart content.

    Args:
        conversation (MutableSequence[Message]): The conversation to inspect.

    Returns:
        bool: True if all messages are a single text/error piece, False otherwise.
    """
    for turn in conversation:
        if len(turn.message_pieces) != 1:
            return False
        if turn.message_pieces[0].converted_value_data_type not in _TEXT_DATA_TYPES:
            return False
    return True


def build_text_chat_messages(conversation: MutableSequence[Message]) -> list[dict[str, Any]]:
    """
    Build chat messages using the simple ``{"role", "content": str}`` format.

    Many OpenAI-"compatible" providers don't support the multipart content format, so the
    plain-text form is used whenever the conversation is text-only.

    Args:
        conversation (MutableSequence[Message]): The conversation to convert. Each message
            must have exactly one text or error piece.

    Returns:
        list[dict[str, Any]]: The list of constructed chat messages.

    Raises:
        ValueError: If any message does not have exactly one text/error piece.
    """
    chat_messages: list[dict[str, Any]] = []
    for message in conversation:
        if len(message.message_pieces) != 1:
            raise ValueError("build_text_chat_messages only supports a single message piece per message.")

        message_piece = message.message_pieces[0]
        if message_piece.converted_value_data_type not in _TEXT_DATA_TYPES:
            raise ValueError(
                f"build_text_chat_messages only supports text and error data types."
                f" Received: {message_piece.converted_value_data_type}."
            )

        chat_message = ChatMessage(role=message_piece.api_role, content=message_piece.converted_value)
        chat_messages.append(chat_message.model_dump(exclude_none=True))

    return chat_messages


def build_text_content_entry(*, message_piece: MessagePiece) -> dict[str, Any]:
    """
    Build a text content part for a multipart chat message.

    Args:
        message_piece (MessagePiece): The text/error piece.

    Returns:
        dict[str, Any]: A ``{"type": "text", "text": ...}`` content part.
    """
    return {"type": "text", "text": message_piece.converted_value}


async def build_image_content_entry_async(*, message_piece: MessagePiece) -> dict[str, Any]:
    """
    Build an image content part (as a base64 data URL) for a multipart chat message.

    Args:
        message_piece (MessagePiece): The ``image_path`` piece.

    Returns:
        dict[str, Any]: A ``{"type": "image_url", "image_url": {"url": ...}}`` content part.
    """
    data_url = await convert_local_image_to_data_url_async(message_piece.converted_value)
    return {"type": "image_url", "image_url": {"url": data_url}}


async def build_audio_content_entry_async(*, message_piece: MessagePiece) -> dict[str, Any]:
    """
    Build an ``input_audio`` content part (base64-encoded) for a multipart chat message.

    Args:
        message_piece (MessagePiece): The ``audio_path`` piece.

    Returns:
        dict[str, Any]: A ``{"type": "input_audio", "input_audio": {"data", "format"}}`` part.

    Raises:
        ValueError: If the audio file extension is not ``.wav`` or ``.mp3``.
    """
    ext = DataTypeSerializer.get_extension(message_piece.converted_value)
    if not ext or ext.lower() not in _INPUT_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio format: {ext}. "
            "OpenAI Chat Completions API input_audio only supports .wav and .mp3. "
            "Note: This is different from the Whisper Speech-to-Text API which supports more formats."
        )
    audio_serializer = data_serializer_factory(
        category="prompt-memory-entries",
        value=message_piece.converted_value,
        data_type="audio_path",
        extension=ext,
    )
    base64_data = await audio_serializer.read_data_base64_async()
    return {"type": "input_audio", "input_audio": {"data": base64_data, "format": ext.lower().lstrip(".")}}


def should_skip_audio_piece(
    *,
    message_piece: MessagePiece,
    is_last_message: bool,
    has_text_piece: bool,
    prefer_transcript_for_history: bool,
) -> bool:
    """
    Determine whether an ``audio_path`` piece should be omitted when building chat messages.

    Assistant audio is always skipped (Chat Completions only accepts audio in user messages;
    the assistant transcript text piece carries the content). Historical user audio is skipped
    when ``prefer_transcript_for_history`` is set and a transcript text piece is present.

    Args:
        message_piece (MessagePiece): The piece to evaluate.
        is_last_message (bool): Whether this is the last (current) message in the conversation.
        has_text_piece (bool): Whether the message also contains a text (transcript) piece.
        prefer_transcript_for_history (bool): Whether to drop historical user audio in favor of
            its transcript.

    Returns:
        bool: True if the audio should be skipped, False otherwise.
    """
    if message_piece.converted_value_data_type != "audio_path":
        return False

    if message_piece.api_role == "assistant":
        return True

    return bool(
        message_piece.api_role == "user" and not is_last_message and has_text_piece and prefer_transcript_for_history
    )


async def build_multimodal_chat_messages_async(
    conversation: MutableSequence[Message],
    *,
    prefer_transcript_for_history: bool = False,
) -> list[dict[str, Any]]:
    """
    Build chat messages using the multipart ``content`` format (text, image, and audio parts).

    Args:
        conversation (MutableSequence[Message]): The conversation to convert.
        prefer_transcript_for_history (bool): Whether to drop historical user audio in favor of
            its transcript (see ``should_skip_audio_piece``).

    Returns:
        list[dict[str, Any]]: The list of constructed chat messages.

    Raises:
        ValueError: If a message has no determinable role, or contains an unsupported data type.
    """
    chat_messages: list[dict[str, Any]] = []
    last_message_index = len(conversation) - 1

    for message_index, message in enumerate(conversation):
        message_pieces = message.message_pieces
        is_last_message = message_index == last_message_index
        has_text_piece = any(mp.converted_value_data_type == "text" for mp in message_pieces)

        content: list[dict[str, Any]] = []
        role = None
        for message_piece in message_pieces:
            role = message_piece.api_role

            if should_skip_audio_piece(
                message_piece=message_piece,
                is_last_message=is_last_message,
                has_text_piece=has_text_piece,
                prefer_transcript_for_history=prefer_transcript_for_history,
            ):
                continue

            data_type = message_piece.converted_value_data_type
            if data_type in _TEXT_DATA_TYPES:
                content.append(build_text_content_entry(message_piece=message_piece))
            elif data_type == "image_path":
                content.append(await build_image_content_entry_async(message_piece=message_piece))
            elif data_type == "audio_path":
                content.append(await build_audio_content_entry_async(message_piece=message_piece))
            else:
                raise ValueError(f"Multimodal data type {data_type} is not yet supported.")

        if not role:
            raise ValueError("No role could be determined from the message pieces.")

        chat_messages.append(ChatMessage(role=role, content=content).model_dump(exclude_none=True))

    return chat_messages


def build_response_format(*, json_config: _JsonResponseConfig) -> dict[str, Any] | None:
    """
    Build the ``response_format`` request parameter from a JSON response config.

    Args:
        json_config (_JsonResponseConfig): The JSON response configuration derived from the
            request metadata.

    Returns:
        dict[str, Any] | None: A ``json_schema`` or ``json_object`` response-format dict, or
        None when JSON output was not requested.
    """
    if not json_config.enabled:
        return None

    if json_config.json_schema:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": json_config.schema_name,
                "schema": json_config.json_schema,
                "strict": json_config.strict,
            },
        }

    return {"type": "json_object"}
