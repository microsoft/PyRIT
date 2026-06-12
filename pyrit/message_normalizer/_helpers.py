# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Internal helpers shared by squash-style message normalizers.

Squash normalizers (e.g. ``HistorySquashNormalizer``,
``GenericSystemSquashNormalizer``) collapse several input messages into one
fresh user-role ``Message`` built via ``Message.from_prompt``. Because that
factory creates a brand-new piece with empty ``prompt_metadata``, callers must
explicitly carry request-level metadata (such as the JSON schema key) forward
so downstream normalizers in the pipeline still see it. ``build_squashed_user_message``
centralizes that propagation rule.
"""

from pyrit.models import Message


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
