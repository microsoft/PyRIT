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

    def to_json(self) -> str:
        """
        Serialize the ChatMessage to a JSON string (deprecated, use ``model_dump_json`` instead).

        Returns:
            A JSON string representation of the message.

        """
        from pyrit.common.deprecation import print_deprecation_message

        print_deprecation_message(
            old_item="ChatMessage.to_json",
            new_item="ChatMessage.model_dump_json",
            removed_in="0.15.0",
        )
        return self.model_dump_json()

    @classmethod
    def from_json(cls, json_str: str) -> "ChatMessage":
        """
        Deserialize a ChatMessage from a JSON string (deprecated, use ``model_validate_json`` instead).

        Args:
            json_str: A JSON string representation of a ChatMessage.

        Returns:
            A ChatMessage instance.

        """
        from pyrit.common.deprecation import print_deprecation_message

        print_deprecation_message(
            old_item="ChatMessage.from_json",
            new_item="ChatMessage.model_validate_json",
            removed_in="0.15.0",
        )
        return cls.model_validate_json(json_str)


class ChatMessagesDataset(BaseModel):
    """
    Represents a dataset of chat messages.
    """

    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    list_of_chat_messages: list[list[ChatMessage]]
