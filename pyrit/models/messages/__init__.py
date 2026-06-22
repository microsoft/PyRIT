# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Messages module - message types and helpers for PyRIT.

- MessagePiece: A single piece of a message exchanged with a target.
- Message: One request/response to a target, made up of one or more pieces.
- ChatMessage: OpenAI-style wire shape consumed/emitted by prompt targets.
- Conversation: Conversation-scoped metadata shared by every piece.
- ConversationReference: Immutable reference to a conversation in an attack.
- conversations: Free functions that operate on collections of messages/pieces.
"""

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

__all__ = [
    "ALLOWED_CHAT_MESSAGE_ROLES",
    "ChatMessage",
    "ChatMessagesDataset",
    "Conversation",
    "ConversationReference",
    "ConversationType",
    "Message",
    "MessagePiece",
    "ToolCall",
    "construct_response_from_request",
    "flatten_to_message_pieces",
    "get_all_values",
    "group_conversation_message_pieces_by_sequence",
    "group_message_pieces_into_conversations",
    "sort_message_pieces",
]
