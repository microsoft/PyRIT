# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The bounded local tool loop shared by targets that execute tools themselves."""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pyrit.models import (
    Message,
    MessagePiece,
    ToolExecutionMetadata,
    ToolLoopStop,
    ToolLoopStopReason,
    construct_response_from_request,
)
from pyrit.prompt_target.common.tool_provider import Tool, ToolInfrastructureError

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_CHARS = 2_000

ToolFunction = Callable[[dict[str, Any]], Awaitable[Any]]
ResolveToolsAsync = Callable[[], Awaitable[tuple[Sequence[Tool], Mapping[str, ToolFunction]]]]


@dataclass(frozen=True)
class ToolDispatchResult:
    """A tool call's output and whether the tool was invoked."""

    output: object
    invoked: bool


@dataclass(frozen=True)
class PendingToolCall:
    """A function call from a model response that the loop must answer."""

    call_id: str
    name: str | None
    arguments: Any
    section: dict[str, Any]


class InvalidToolCallsError(Exception):
    """A response's tool calls cannot be run, so the turn ends without running any of them."""


async def dispatch_tool_call_async(
    *, call: PendingToolCall, resolve_tools_async: ResolveToolsAsync, fail_on_missing_function: bool = False
) -> ToolDispatchResult:
    """
    Run one function call with the matching tool.

    Args:
        call: The function call.
        resolve_tools_async: Returns the registered tools and custom functions. Called only once the
            call's arguments parse.
        fail_on_missing_function: Raise instead of returning an error payload for a call that cannot run.

    Returns:
        The output payload and whether the tool was invoked. A call that cannot run returns an error
        payload with invoked=False, so the model can correct it.

    Raises:
        ValueError: If the call has no name or malformed arguments and ``fail_on_missing_function`` is set.
        KeyError: If the function is not registered and ``fail_on_missing_function`` is set.
    """
    name = call.name
    if not name:
        if fail_on_missing_function:
            raise ValueError("Function call section missing 'name' field")
        return ToolDispatchResult(
            output={"error": "missing_function_name", "tool_call_section": call.section}, invoked=False
        )

    args_json = call.arguments
    try:
        args = json.loads(args_json)
    except Exception:
        if fail_on_missing_function:
            raise ValueError(f"Malformed arguments for function '{name}': {args_json}") from None
        logger.warning("Malformed arguments for function '%s': %s", name, args_json)
        return ToolDispatchResult(
            output={"error": "malformed_arguments", "function": name, "raw_arguments": args_json}, invoked=False
        )
    tools, custom_functions = await resolve_tools_async()
    configured_tool = next((tool for tool in tools if tool.name == name), None)
    custom_function = custom_functions.get(name)
    if (configured_tool is not None or custom_function is not None) and not isinstance(args, dict):
        if fail_on_missing_function:
            raise ValueError(f"Arguments for function '{name}' must be a JSON object")
        return ToolDispatchResult(
            output={"error": "malformed_arguments", "function": name, "raw_arguments": args_json}, invoked=False
        )

    if configured_tool is not None:
        try:
            configured_tool.validate_arguments(arguments=args)
        except ValueError as exc:
            if fail_on_missing_function:
                raise
            logger.warning("Invalid arguments for function '%s': %s", name, exc)
            return ToolDispatchResult(
                output={
                    "error": "malformed_arguments",
                    "function": name,
                    "raw_arguments": args_json,
                    "message": str(exc)[:MAX_ERROR_MESSAGE_CHARS],
                },
                invoked=False,
            )
        return ToolDispatchResult(output=await configured_tool.execute_async(arguments=args), invoked=True)

    if custom_function is not None:
        return ToolDispatchResult(output=await custom_function(args), invoked=True)

    if fail_on_missing_function:
        raise KeyError(f"Function '{name}' is not registered")
    available_functions = sorted({*(tool.name for tool in tools), *custom_functions})
    logger.warning("Function '%s' not registered. Available: %s", name, available_functions)
    return ToolDispatchResult(
        output={"error": "function_not_found", "missing_function": name, "available_functions": available_functions},
        invoked=False,
    )


def make_tool_result_piece(*, result: ToolDispatchResult, call_id: str, reference_piece: MessagePiece) -> MessagePiece:
    """
    Create the function_call_output piece that answers a call.

    Args:
        result: The dispatch result to record.
        call_id: The call it answers.
        reference_piece: A piece to copy conversation context from.

    Returns:
        A tool piece recording the output and whether the tool was invoked.
    """
    output = result.output
    output_str = output if isinstance(output, str) else json.dumps(output, separators=(",", ":"))
    return MessagePiece(
        role="tool",
        original_value=json.dumps(
            {"type": "function_call_output", "call_id": call_id, "output": output_str}, separators=(",", ":")
        ),
        original_value_data_type="function_call_output",
        conversation_id=reference_piece.conversation_id,
        prompt_metadata=ToolExecutionMetadata(invoked=result.invoked).to_metadata(),
    )


def tool_loop_error(*, request_piece: MessagePiece, detail: str, reason: ToolLoopStopReason) -> Message:
    """
    Build the error response that ends a turn whose tools could not continue.

    Args:
        request_piece: The request the turn answers.
        detail: Why the turn ended.
        reason: The machine-readable reason, recorded in the response's metadata.

    Returns:
        An error response, which history replay skips.
    """
    logger.error(detail)
    return construct_response_from_request(
        request=request_piece,
        response_text_pieces=[detail],
        response_type="error",
        prompt_metadata=ToolLoopStop(reason=reason).to_metadata(),
        error="processing",
    )


async def run_tool_loop_async(
    *,
    conversation: Sequence[Message],
    request_piece: MessagePiece,
    send_async: Callable[[Sequence[Message]], Awaitable[Message]],
    find_calls: Callable[[Message], list[PendingToolCall]],
    dispatch_async: Callable[[PendingToolCall], Awaitable[ToolDispatchResult]],
    max_tool_rounds: int,
) -> list[Message]:
    """
    Send a conversation and run the model's tool calls until it answers or the loop stops.

    Send failures and tool exceptions other than ``ToolInfrastructureError`` propagate. Every early stop
    answers the pending calls and ends with an error response carrying a ``ToolLoopStop`` reason.

    Args:
        conversation: The conversation to send, ending with the request.
        request_piece: The request the turn answers.
        send_async: Sends the working conversation and returns the model's response.
        find_calls: Returns a response's function calls in order, or raises ``InvalidToolCallsError``.
        dispatch_async: Runs one call.
        max_tool_rounds: How many rounds of calls may run before the loop stops.

    Returns:
        Every response and tool result for the normalizer to persist, in order.
    """
    working = list(conversation)
    responses: list[Message] = []
    rounds = 0
    while True:
        logger.info("Sending conversation with %d messages to the prompt target", len(working))
        result = await send_async(working)
        working.append(result)
        responses.append(result)

        try:
            calls = find_calls(result)
        except InvalidToolCallsError as error:
            for piece in result.message_pieces:
                piece.response_error = "processing"
            stop = ToolLoopStopReason.INVALID_TOOL_CALLS
            responses.append(tool_loop_error(request_piece=request_piece, detail=str(error), reason=stop))
            return responses
        if not calls:
            return responses

        if rounds >= max_tool_rounds:
            responses.extend(
                _answer_without_running(
                    calls=calls, output={"error": "max_tool_rounds_exhausted"}, reference_piece=request_piece
                )
            )
            detail = f"max_tool_rounds={max_tool_rounds} exhausted."
            stop = ToolLoopStopReason.MAX_TOOL_ROUNDS_EXHAUSTED
            responses.append(tool_loop_error(request_piece=request_piece, detail=detail, reason=stop))
            return responses
        rounds += 1

        tool_messages, failure = await _execute_round_async(
            calls=calls, dispatch_async=dispatch_async, reference_piece=request_piece
        )
        working.extend(tool_messages)
        responses.extend(tool_messages)
        if failure is not None:
            stop = ToolLoopStopReason.TOOL_INFRASTRUCTURE_ERROR
            responses.append(tool_loop_error(request_piece=request_piece, detail=failure, reason=stop))
            return responses


async def _execute_round_async(
    *,
    calls: Sequence[PendingToolCall],
    dispatch_async: Callable[[PendingToolCall], Awaitable[ToolDispatchResult]],
    reference_piece: MessagePiece,
) -> tuple[list[Message], str | None]:
    """
    Run one response's calls in order, stopping at a tool infrastructure failure.

    Returns:
        One result message per call, and why the turn must end, or None to continue. After a
        failure, the calls not yet run get error results, so every call is answered.
    """
    messages: list[Message] = []
    for index, call in enumerate(calls):
        try:
            result = await dispatch_async(call)
        except ToolInfrastructureError as error:
            logger.error("Tool %r could not run; ending the turn: %s", call.name, error)
            # The model sees this result again on later turns, so it gets no infrastructure details;
            # they stay in the error response, which history replay skips.
            failed = ToolDispatchResult(
                output={"error": "tool_infrastructure_error", "message": "The tool's infrastructure failed."},
                invoked=error.invoked,
            )
            messages.append(_tool_message(result=failed, call=call, reference_piece=reference_piece))
            messages.extend(
                _answer_without_running(
                    calls=calls[index + 1 :],
                    output={"error": "tool_not_executed", "message": "An earlier tool call failed."},
                    reference_piece=reference_piece,
                )
            )
            return messages, f"Tool {call.name!r} could not run: {error}"
        messages.append(_tool_message(result=result, call=call, reference_piece=reference_piece))
    return messages, None


def _answer_without_running(
    *, calls: Sequence[PendingToolCall], output: dict[str, str], reference_piece: MessagePiece
) -> list[Message]:
    result = ToolDispatchResult(output=output, invoked=False)
    return [_tool_message(result=result, call=call, reference_piece=reference_piece) for call in calls]


def _tool_message(*, result: ToolDispatchResult, call: PendingToolCall, reference_piece: MessagePiece) -> Message:
    piece = make_tool_result_piece(result=result, call_id=call.call_id, reference_piece=reference_piece)
    return Message(message_pieces=[piece])
