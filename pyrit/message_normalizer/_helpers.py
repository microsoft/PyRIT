# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Internal helpers for squash-style message normalizers.

Some squash normalizers, such as ``GenericSystemSquashNormalizer``, collapse
several input messages into one fresh user-role ``Message`` built via
``Message.from_prompt``. Because that factory creates a brand-new piece with
empty ``prompt_metadata``, callers must explicitly carry request-level metadata
forward so downstream normalizers still see it.
``build_squashed_user_message`` centralizes that propagation rule.

This module also owns the shared rule for whether converter output can be
flattened into text without losing media semantics.
"""

from collections.abc import Sequence

from pyrit.models import Message, MessagePiece, PromptDataType


def get_unflattenable_converter_output_types(
    *,
    converted_messages: Sequence[Message],
    source_messages: Sequence[Message] | None = None,
) -> set[PromptDataType]:
    """
    Return converter output types that cannot be represented by text flattening.

    Stored history is treated as converted when its original and converted
    representations differ. When source messages are provided, only converters
    added by the current conversion pass count, and ephemeral pieces are ignored.

    Args:
        converted_messages (Sequence[Message]): Messages containing converter output.
        source_messages (Sequence[Message] | None): Optional messages from before
            the current conversion pass.

    Returns:
        set[PromptDataType]: Non-text converter output types.
    """
    if source_messages is None:
        converted_pieces = (
            piece
            for message in converted_messages
            for piece in message.message_pieces
            if piece.original_value != piece.converted_value
            or piece.original_value_data_type != piece.converted_value_data_type
        )
    else:
        converted_pieces = (
            converted_piece
            for source_message, converted_message in zip(source_messages, converted_messages, strict=True)
            for source_piece, converted_piece in zip(
                source_message.message_pieces,
                converted_message.message_pieces,
                strict=True,
            )
            if len(converted_piece.converter_identifiers) > len(source_piece.converter_identifiers)
            and not converted_piece.not_in_memory
        )

    return {piece.converted_value_data_type for piece in converted_pieces if piece.converted_value_data_type != "text"}


def format_message_piece_for_context(*, piece: MessagePiece) -> str:
    """
    Format one message piece for inclusion in textual conversation history.

    Non-text pieces use their context description when available and otherwise
    use a modality placeholder, so local asset paths are not exposed as prompt
    text.

    Args:
        piece (MessagePiece): The piece to represent as context.

    Returns:
        str: The textual representation of the piece.
    """
    data_type = piece.converted_value_data_type or piece.original_value_data_type
    if data_type != "text":
        description = piece.prompt_metadata.get("context_description")
        if description:
            return f"[{data_type.capitalize()} - {description}]"
        return f"[{data_type.capitalize()}]"

    if piece.original_value != piece.converted_value:
        return f"{piece.converted_value} (original: {piece.original_value})"
    return piece.converted_value


def build_squashed_user_message(*, new_message_content: str, source_messages: list[Message]) -> Message:
    """
    Build a fresh user-role ``Message`` that subsumes ``source_messages``.

    The last source message's ``prompt_metadata`` is propagated onto the new
    piece so downstream normalizers (e.g. ``JsonSchemaNormalizer``) still see
    request-level metadata such as the JSON schema key. Without this, a fresh
    piece from ``Message.from_prompt`` would have empty metadata and any
    subsequent capability adaptation would silently no-op.

    Args:
        new_message_content: The combined text content for the new piece.
        source_messages: The messages being subsumed. The LAST message's
            first piece supplies the ``prompt_metadata`` carried onto the new
            piece. Must be non-empty.

    Returns:
        Message: A single-piece user-role message carrying the propagated metadata.

    Raises:
        ValueError: If ``source_messages`` is empty.
    """
    if not source_messages:
        raise ValueError("source_messages must not be empty")

    last_message = source_messages[-1]
    propagated_metadata = dict(last_message.message_pieces[0].prompt_metadata)
    return Message.from_prompt(prompt=new_message_content, role="user", prompt_metadata=propagated_metadata)
