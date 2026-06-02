# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Backward-compatibility shim.

``Message`` and the conversation helpers now live in ``pyrit.models.messages``.
Import from there (or from ``pyrit.models``) instead. This module re-exports the
public names so existing ``from pyrit.models.message import ...`` imports keep
working.
"""

from typing import Any

from pyrit.models.messages import message as _message
from pyrit.models.messages.conversations import (
    construct_response_from_request,
    flatten_to_message_pieces,
    get_all_values,
    group_conversation_message_pieces_by_sequence,
    group_message_pieces_into_conversations,
)
from pyrit.models.messages.message import Message


def __getattr__(name: str) -> Any:
    return getattr(_message, name)


__all__ = [
    "Message",
    "construct_response_from_request",
    "flatten_to_message_pieces",
    "get_all_values",
    "group_conversation_message_pieces_by_sequence",
    "group_message_pieces_into_conversations",
]
