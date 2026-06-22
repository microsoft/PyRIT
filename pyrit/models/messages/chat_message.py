# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
OpenAI-format chat message types.

``ChatMessage`` is the OpenAI Chat Completions wire shape — a ``role`` plus a
string-or-multipart ``content``, with the OpenAI ``name`` / ``tool_calls`` /
``tool_call_id`` fields. Prompt targets that speak the OpenAI API (and the many
providers that mirror it) consume and emit these objects directly.

It is intentionally distinct from the PyRIT domain ``Message`` / ``MessagePiece``
types in this same package: those model a persisted request/response exchange,
whereas ``ChatMessage`` is the lightweight OpenAI-shaped transport representation
handed to a model API.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict

from pyrit.models.literals import ChatMessageRole

ALLOWED_CHAT_MESSAGE_ROLES = ["system", "user", "assistant", "simulated_assistant", "tool", "developer"]


class ToolCall(BaseModel):
    """Represents a tool invocation requested by the assistant."""

    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    function: str


class ChatMessage(BaseModel):
    """
    Represents a single OpenAI Chat Completions message.

    Mirrors the OpenAI message schema. The content field can be:
    - A simple string for single-part text messages
    - A list of dicts for multipart messages (e.g., text + images)
    """

    model_config = ConfigDict(extra="forbid")
    role: ChatMessageRole
    content: str | list[dict[str, Any]]
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the ChatMessage to a dictionary.

        Returns:
            A dictionary representation of the message, excluding None values.

        """
        return self.model_dump(exclude_none=True)


class ChatMessagesDataset(BaseModel):
    """
    Represents a dataset of chat messages.
    """

    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    list_of_chat_messages: list[list[ChatMessage]]
