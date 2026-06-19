# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

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
    Represents a chat message for API consumption.

    The content field can be:
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
