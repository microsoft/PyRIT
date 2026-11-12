# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Parsing and validation for the function-call history shapes stored by PyRIT targets."""

import json
from collections.abc import Sequence

import referencing
import referencing.exceptions
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from pyrit.models import Message, MessagePiece, PromptDataType, ToolCall, ToolDefinition
from pyrit.models.messages.chat_message import FunctionCall
from pyrit.models.messages.tool_content import FunctionCallContent, FunctionOutputContent

TOOL_CALL_INPUT_MODALITIES: frozenset[frozenset[PromptDataType]] = frozenset(
    {frozenset({"function_call"}), frozenset({"text", "function_call"}), frozenset({"function_call_output"})}
)


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


def check_simulated_tool_history(*, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> list[str]:
    """
    List every way a fabricated tool history breaks the given tool contracts or call ordering.

    Each function call must name one of the tools, pass arguments that satisfy its JSON Schema,
    and be answered by its output before the next message. The history needs at least one
    answered call. Schemas resolve only their own references; remote references are reported,
    never retrieved. Tool outputs are synthetic and are not checked for plausibility.

    Args:
        messages (Sequence[Message]): The ordered history, excluding the live request.
        tools (Sequence[ToolDefinition]): The tools the history may call.

    Returns:
        list[str]: Human-readable issues, or an empty list when the history is valid.
    """
    checker = _SimulatedToolHistoryChecker(tools=tools)
    for index, message in enumerate(messages):
        checker.check_message(message=message, index=index)
    return checker.finish()


class _SimulatedToolHistoryChecker:
    """Collect issues while walking a history once, in order."""

    def __init__(self, *, tools: Sequence[ToolDefinition]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._issues: list[str] = []
        self._pending: set[str] = set()
        self._call_ids: set[str] = set()
        self._answered = 0

    def check_message(self, *, message: Message, index: int) -> None:
        if self._pending and message.api_role != "tool":
            self._issues.append(
                f"Message {index}: tool call(s) {sorted(self._pending)} must be answered by their outputs "
                "before the next message."
            )
            self._pending.clear()
        for piece in message.message_pieces:
            self._check_piece(piece=piece, label=f"Message {index}")

    def finish(self) -> list[str]:
        if self._pending:
            self._issues.append(f"Tool call(s) {sorted(self._pending)} are never answered by their outputs.")
        if not self._answered:
            self._issues.append("The history must contain at least one tool call answered by its output.")
        return self._issues

    def _check_piece(self, *, piece: MessagePiece, label: str) -> None:
        data_type = piece.converted_value_data_type
        if data_type == "function_call":
            self._check_call(piece=piece, label=label)
        elif data_type == "function_call_output":
            self._check_output(piece=piece, label=label)
        elif data_type != "text":
            self._issues.append(f"{label}: '{data_type}' content is not supported in a simulated tool history.")
        elif piece.api_role not in ("user", "assistant"):
            self._issues.append(f"{label}: text must come from the user or the assistant, not '{piece.api_role}'.")

    def _check_call(self, *, piece: MessagePiece, label: str) -> None:
        if piece.api_role != "assistant":
            self._issues.append(f"{label}: function calls must come from the assistant.")
        try:
            content = FunctionCallContent.model_validate_json(piece.converted_value)
            call_id = content.validated_call_id()
            function = content.validated_function()
        except ValueError as error:
            self._issues.append(f"{label}: invalid function call: {_describe(error)}")
            return
        if call_id in self._call_ids:
            self._issues.append(f"{label}: call_id '{call_id}' is reused; every call needs a unique call_id.")
        self._call_ids.add(call_id)
        self._pending.add(call_id)
        self._issues.extend(
            f"{label}: {issue}"
            for issue in _check_arguments(tools=self._tools, name=function.name, arguments=function.arguments)
        )

    def _check_output(self, *, piece: MessagePiece, label: str) -> None:
        if piece.api_role != "tool":
            self._issues.append(f"{label}: function call outputs must have the tool role.")
        try:
            call_id = FunctionOutputContent.model_validate_json(piece.converted_value).call_id
        except ValidationError as error:
            self._issues.append(f"{label}: invalid function call output: {_describe(error)}")
            return
        if call_id not in self._pending:
            self._issues.append(f"{label}: output for '{call_id}' does not answer a pending call.")
            return
        self._pending.remove(call_id)
        self._answered += 1


_OFFLINE_SCHEMA_REGISTRY: referencing.Registry = referencing.Registry()


def _check_arguments(*, tools: dict[str, ToolDefinition], name: str, arguments: str) -> list[str]:
    tool = tools.get(name)
    if tool is None:
        return [f"'{name}' is not an available tool. Available tools: {sorted(tools)}."]
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as error:
        return [f"arguments for '{name}' are not valid JSON: {error}."]
    if not isinstance(parsed, dict):
        return [f"arguments for '{name}' must be a JSON object."]
    try:
        Draft202012Validator.check_schema(tool.parameters)
        errors = list(Draft202012Validator(tool.parameters, registry=_OFFLINE_SCHEMA_REGISTRY).iter_errors(parsed))
    except (SchemaError, referencing.exceptions.Unresolvable):
        return [f"cannot check arguments for '{name}': its parameter schema is invalid or not self-contained."]
    return [
        f"arguments for '{name}' at {error.json_path}: {error.message}."
        for error in sorted(errors, key=lambda error: (error.json_path, error.message))
    ]


def _describe(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, detail['loc'])) or 'value'}: {detail['msg']}" for detail in error.errors()
        )
    return str(error)
