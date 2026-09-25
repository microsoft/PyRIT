# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Validation of persisted function-call context, without executing tools."""

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.messages.message import Message


class FunctionArguments(BaseModel):
    """The function payload used in Chat Completions call pieces."""

    model_config = ConfigDict(extra="allow")
    name: str = Field(min_length=1)
    arguments: str


class FunctionCallContent(BaseModel):
    """Validate either supported function-call representation without rewriting it."""

    model_config = ConfigDict(extra="allow")
    id: str | None = None
    call_id: str | None = None
    function: FunctionArguments | None = None
    name: str | None = None
    arguments: str | None = None

    def validated_call_id(self) -> str:
        """
        Return the call ID after checking the function name and JSON arguments.

        Returns:
            The validated call ID.

        Raises:
            ValueError: Required fields or JSON-object arguments are missing.
        """
        call_id = self.call_id or self.id
        name = self.function.name if self.function else self.name
        arguments = self.function.arguments if self.function else self.arguments
        if not call_id or not call_id.strip() or not name or not name.strip() or arguments is None:
            raise ValueError("A function call requires a call ID, function name, and arguments")
        if not isinstance(json.loads(arguments), dict):
            raise ValueError("Function arguments must be a JSON object")
        return call_id


class FunctionOutputContent(BaseModel):
    """A response linked to a preceding function call."""

    model_config = ConfigDict(extra="allow")
    call_id: str = Field(min_length=1)
    output: Any


def validate_tool_conversation(messages: Sequence[Message]) -> None:
    """
    Validate function-call links and payloads without modifying stored values.

    Raises:
        ValueError: A role, payload, or call/response link is invalid.
    """
    calls: set[str] = set()
    responses: set[str] = set()
    for message in messages:
        for piece in message.message_pieces:
            data_type = piece.converted_value_data_type
            if data_type == "function_call":
                if message.api_role != "assistant":
                    raise ValueError("Function calls require an assistant role")
                call = FunctionCallContent.model_validate_json(piece.converted_value)
                call_id = call.validated_call_id()
                if call_id in calls:
                    raise ValueError(f"Duplicate function call ID: {call_id}")
                calls.add(call_id)
            elif data_type == "function_call_output":
                output = FunctionOutputContent.model_validate_json(piece.converted_value)
                if message.api_role != "tool":
                    raise ValueError("Function outputs require the tool role")
                if output.call_id not in calls or output.call_id in responses:
                    raise ValueError(f"Tool response needs one preceding, unanswered call: {output.call_id}")
                responses.add(output.call_id)
            elif data_type == "tool_call":
                if message.api_role != "assistant":
                    raise ValueError("Tool calls require an assistant role")
                if not isinstance(json.loads(piece.converted_value), dict):
                    raise ValueError("Tool content must be a JSON object")
