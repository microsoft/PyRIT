# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Parsing for the function-call history shapes already stored by PyRIT targets."""

import json
from typing import Any

from pyrit.models import MessagePiece, PromptDataType, ToolCall
from pyrit.models.messages.chat_message import FunctionCall

TOOL_CALL_INPUT_MODALITIES: frozenset[frozenset[PromptDataType]] = frozenset(
    {frozenset({"function_call"}), frozenset({"text", "function_call"}), frozenset({"function_call_output"})}
)


def _read_payload(piece: MessagePiece) -> dict[str, Any]:
    """
    Read a structured target-facing history payload.

    Returns:
        dict[str, Any]: The converted JSON object.

    Raises:
        ValueError: If the value is not a JSON object.
    """
    payload = json.loads(piece.converted_value)
    if not isinstance(payload, dict):
        raise ValueError("Tool history must contain a JSON object.")
    return payload


def parse_function_call(piece: MessagePiece) -> ToolCall:
    """
    Read a Chat Completions or Responses function call without changing its arguments.

    Args:
        piece: The assistant function-call piece.

    Returns:
        ToolCall: The call in Chat Completions form.

    Raises:
        ValueError: If the role or structured fields are invalid.
        KeyError: If a required payload field is absent.
    """
    if piece.api_role != "assistant":
        raise ValueError("Function calls must have an assistant or simulated_assistant role.")
    payload = _read_payload(piece)
    if payload["type"] == "function_call":
        payload = {
            "type": "function",
            "id": payload.get("call_id"),
            "function": {"name": payload.get("name"), "arguments": payload.get("arguments")},
        }
    call = ToolCall.model_validate(payload)
    if call.type != "function" or not call.id or not isinstance(call.function, FunctionCall) or not call.function.name:
        raise ValueError("Tool history requires a function call with an ID, name, and serialized arguments.")
    return call


def parse_function_call_output(piece: MessagePiece) -> tuple[str, str]:
    """
    Read a function result as its call ID and string output.

    Args:
        piece: The function-result piece.

    Returns:
        tuple[str, str]: The call ID and serialized output.

    Raises:
        ValueError: If the result payload is invalid.
        KeyError: If the call ID is absent.
    """
    payload = _read_payload(piece)
    call_id = payload["call_id"]
    if payload.get("type") != "function_call_output" or not isinstance(call_id, str) or not call_id:
        raise ValueError("Function call output requires type='function_call_output' and a nonempty call_id.")
    if "output" not in payload:
        raise ValueError("Function call output requires an output field.")
    output = payload["output"]
    return call_id, output if isinstance(output, str) else json.dumps(output, separators=(",", ":"))
