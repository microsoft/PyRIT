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
"""

from pyrit.models import Message, MessagePiece


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
