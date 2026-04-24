# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any, Optional, Union

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
    content: Union[str, list[dict[str, Any]]]
    name: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None
    tool_call_id: Optional[str] = None

    def to_json(self) -> str:
        """
        Serialize the ChatMessage to a JSON string.

        Returns:
            A JSON string representation of the message.

        """
        return self.model_dump_json()

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the ChatMessage to a dictionary.

        Returns:
            A dictionary representation of the message, excluding None values.

        """
        return self.model_dump(exclude_none=True)

    @classmethod
    def from_json(cls, json_str: str) -> "ChatMessage":
        """
        Deserialize a ChatMessage from a JSON string.

        Args:
            json_str: A JSON string representation of a ChatMessage.

        Returns:
            A ChatMessage instance.

        """
        return cls.model_validate_json(json_str)


class ChatMessagesDataset(BaseModel):
    """
    Represents a dataset of chat messages.
    """

    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    list_of_chat_messages: list[list[ChatMessage]]
