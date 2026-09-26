# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Validation of stored tool content, without executing tools or changing messages."""

import json
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.messages.chat_message import FunctionCall
from pyrit.models.messages.message import Message


class FunctionArguments(FunctionCall):
    """A function name and serialized JSON-object arguments, with provider extensions."""

    model_config = ConfigDict(extra="allow")
    name: str = Field(min_length=1, pattern=r"\S")


class FunctionCallContent(BaseModel):
    """Either the Chat Completions or Responses representation of a function call."""

    model_config = ConfigDict(extra="allow")
    type: Literal["function", "function_call"] | None = None
    id: str | None = None
    call_id: str | None = None
    function: FunctionArguments | None = None
    name: str | None = None
    arguments: str | None = None

    def validated_function(self) -> FunctionArguments:
        """
        Read the function name and JSON-object arguments without rewriting them.

        Returns:
            FunctionArguments: The validated function payload.

        Raises:
            ValueError: The name or serialized JSON-object arguments are invalid.
        """
        function = self.function
        if function is None:
            if self.name is None or self.arguments is None:
                raise ValueError("A function call requires a function name and arguments")
            function = FunctionArguments(name=self.name, arguments=self.arguments)
        if not isinstance(json.loads(function.arguments), dict):
            raise ValueError("Function arguments must be a JSON object")
        return function

    def validated_call_id(self) -> str:
        """
        Validate the function payload and return its call ID.

        Returns:
            str: The validated call ID.

        Raises:
            ValueError: The call ID, function name, or arguments are invalid.
        """
        call_id = self.call_id or self.id
        if not call_id or not call_id.strip():
            raise ValueError("A function call requires a nonempty call ID")
        self.validated_function()
        return call_id


class FunctionOutputContent(BaseModel):
    """A result linked to a preceding function call."""

    model_config = ConfigDict(extra="allow")
    type: Literal["function_call_output"] | None = None
    call_id: str = Field(min_length=1, pattern=r"\S")
    output: Any


def validate_tool_conversation(messages: Sequence[Message]) -> None:
    """
    Validate tool payloads, roles, and call/result links in complete stored history.

    Empty history and unanswered calls are permitted so drafts can end at an
    assistant turn. Provider-specific tool schemas and replay support are checked
    by targets. This function does not normalize or modify the supplied messages.

    Args:
        messages: Ordered history containing the calls for any included results.

    Raises:
        ValueError: A role, payload, or call/result link is invalid.
    """
    calls: set[str] = set()
    responses: set[str] = set()
    for message in messages:
        for piece in message.message_pieces:
            data_type = piece.converted_value_data_type
            if data_type == "function_call":
                if piece.api_role != "assistant":
                    raise ValueError("Function calls require an assistant role")
                call = FunctionCallContent.model_validate_json(piece.converted_value)
                call_id = call.validated_call_id()
                if call_id in calls:
                    raise ValueError(f"Duplicate function call ID: {call_id}")
                calls.add(call_id)
            elif data_type == "function_call_output":
                if piece.api_role != "tool":
                    raise ValueError("Function outputs require the tool role")
                output = FunctionOutputContent.model_validate_json(piece.converted_value)
                if output.call_id not in calls or output.call_id in responses:
                    raise ValueError(f"Tool response needs one preceding, unanswered call: {output.call_id}")
                responses.add(output.call_id)
            elif data_type == "tool_call":
                if piece.api_role != "assistant":
                    raise ValueError("Tool calls require an assistant role")
                if not isinstance(json.loads(piece.converted_value), dict):
                    raise ValueError("Tool content must be a JSON object")
