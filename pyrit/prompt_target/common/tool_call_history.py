# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Parsing for the function-call history shapes already stored by PyRIT targets."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pyrit.models import MessagePiece, PromptDataType, ToolCall, ToolDefinition
from pyrit.models.messages.chat_message import FunctionCall
from pyrit.models.messages.tool_content import FunctionCallContent, FunctionOutputContent

TOOL_CALL_INPUT_MODALITIES: frozenset[frozenset[PromptDataType]] = frozenset(
    {frozenset({"function_call"}), frozenset({"text", "function_call"}), frozenset({"function_call_output"})}
)


def parse_function_tool_declarations(declarations: Sequence[Mapping[str, Any]]) -> list[ToolDefinition]:
    """
    Read function tool declarations as they are sent to the endpoint.

    Accepts the Responses form (``{"type": "function", "name": ...}``) and the Chat Completions
    form (``{"type": "function", "function": {"name": ...}}``). Other tool types, such as hosted
    web search, are not function tools and are skipped.

    Args:
        declarations: The ``tools`` request parameter.

    Returns:
        list[ToolDefinition]: The declared function tools.
    """
    definitions: list[ToolDefinition] = []
    for declaration in declarations:
        if declaration.get("type") != "function":
            continue
        function = declaration.get("function", declaration)
        definitions.append(
            ToolDefinition(
                name=function["name"],
                description=function.get("description") or "",
                parameters=function.get("parameters") or {"type": "object", "properties": {}},
            )
        )
    return definitions


def parse_function_call(piece: MessagePiece) -> ToolCall:
    """
    Read a Chat Completions or Responses function call without changing its arguments.

    Args:
        piece: The assistant function-call piece.

    Returns:
        ToolCall: The call in Chat Completions form.

    Raises:
        ValueError: If the role or structured fields are invalid.
    """
    if piece.api_role != "assistant":
        raise ValueError("Function calls must have an assistant or simulated_assistant role.")
    payload = FunctionCallContent.model_validate_json(piece.converted_value)
    call_id = payload.validated_call_id()
    function = payload.validated_function()
    return ToolCall(
        type="function",
        id=call_id,
        function=FunctionCall(name=function.name, arguments=function.arguments),
    )


def parse_function_call_output(piece: MessagePiece) -> tuple[str, str]:
    """
    Read a function result as its call ID and string output.

    Args:
        piece: The function-result piece.

    Returns:
        tuple[str, str]: The call ID and serialized output.

    Raises:
        ValueError: If the result payload is invalid.
    """
    payload = FunctionOutputContent.model_validate_json(piece.converted_value)
    output = payload.output
    return payload.call_id, output if isinstance(output, str) else json.dumps(output, separators=(",", ":"))
