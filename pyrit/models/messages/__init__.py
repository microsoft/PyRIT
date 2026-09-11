# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Messages module - message types and helpers for PyRIT.

- MessagePiece: A single piece of a message exchanged with a target.
- Message: One request/response to a target, made up of one or more pieces.
- ChatMessage: OpenAI-style wire shape consumed/emitted by prompt targets.
- Conversation: Conversation-scoped metadata shared by every piece.
- ConversationReference: Immutable reference to a conversation in an attack.
- conversations: Free functions that operate on collections of messages/pieces.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models.messages.chat_message import (
        ALLOWED_CHAT_MESSAGE_ROLES,
        ChatMessage,
        ChatMessagesDataset,
        ToolCall,
    )
    from pyrit.models.messages.conversation_reference import ConversationReference, ConversationType
    from pyrit.models.messages.conversations import (
        Conversation,
        construct_response_from_request,
        flatten_to_message_pieces,
        get_all_values,
        group_conversation_message_pieces_by_sequence,
        group_message_pieces_into_conversations,
    )
    from pyrit.models.messages.message import Message
    from pyrit.models.messages.message_piece import MessagePiece, sort_message_pieces

_LAZY_EXPORTS: dict[str, str] = {
    "ALLOWED_CHAT_MESSAGE_ROLES": "pyrit.models.messages.chat_message",
    "ChatMessage": "pyrit.models.messages.chat_message",
    "ChatMessagesDataset": "pyrit.models.messages.chat_message",
    "Conversation": "pyrit.models.messages.conversations",
    "ConversationReference": "pyrit.models.messages.conversation_reference",
    "ConversationType": "pyrit.models.messages.conversation_reference",
    "Message": "pyrit.models.messages.message",
    "MessagePiece": "pyrit.models.messages.message_piece",
    "ToolCall": "pyrit.models.messages.chat_message",
    "construct_response_from_request": "pyrit.models.messages.conversations",
    "flatten_to_message_pieces": "pyrit.models.messages.conversations",
    "get_all_values": "pyrit.models.messages.conversations",
    "group_conversation_message_pieces_by_sequence": "pyrit.models.messages.conversations",
    "group_message_pieces_into_conversations": "pyrit.models.messages.conversations",
    "sort_message_pieces": "pyrit.models.messages.message_piece",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    """
    Resolve a public message export on first access.

    Args:
        name (str): The requested public name.

    Returns:
        object: The resolved export.
    """
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    """Return package attributes, including unresolved exports."""
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
