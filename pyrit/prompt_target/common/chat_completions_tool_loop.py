# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The bounded local tool loop for targets that speak the Chat Completions wire format."""

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from typing import Any

from pyrit.models import Message
from pyrit.models.messages.chat_message import FunctionCall
from pyrit.prompt_target.common.tool_call_history import parse_function_call
from pyrit.prompt_target.common.tool_loop import (
    InvalidToolCallsError,
    PendingToolCall,
    ToolFunction,
    dispatch_tool_call_async,
    run_tool_loop_async,
)
from pyrit.prompt_target.common.tool_provider import Tool, ToolProvider, _ScopedToolProvider, collect_tools_async

SendCompletionAsync = Callable[[list[Message], Sequence[Tool]], Awaitable[list[Message]]]


def validate_tool_loop_settings(*, max_tool_rounds: int, runs_tools: bool, n: object) -> None:
    """
    Check a target's tool settings when it is constructed.

    Args:
        max_tool_rounds: The most completions per send whose calls run locally.
        runs_tools: Whether the target executes configured tools itself.
        n: The number of completions each request asks for.

    Raises:
        ValueError: If ``max_tool_rounds`` is not a nonnegative integer, or tools run locally with ``n`` above 1.
    """
    if isinstance(max_tool_rounds, bool) or not isinstance(max_tool_rounds, int) or max_tool_rounds < 0:
        raise ValueError("max_tool_rounds must be a nonnegative integer.")
    if runs_tools and n not in (None, 1):
        raise ValueError("Automatic tool execution requires n=1.")


def chat_tool_definition(tool: Tool) -> dict[str, Any]:
    """
    Describe a tool as a Chat Completions function tool.

    Args:
        tool: The tool to advertise.

    Returns:
        dict[str, Any]: The request's ``tools`` entry.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "strict": tool.strict,
        },
    }


def tool_identifier_params(
    *, tools: Sequence[Tool], tool_providers: Sequence[ToolProvider], execute_tools: bool, max_tool_rounds: int
) -> dict[str, Any]:
    """
    Return the identifier params for a target's tool settings, all None for a target without tools.

    Args:
        tools: The configured tools.
        tool_providers: The configured tool providers.
        execute_tools: Whether returned calls run locally.
        max_tool_rounds: The most completions per send whose calls run locally.

    Returns:
        dict[str, Any]: The ``tools``, ``tool_providers``, ``execute_tools`` and ``max_tool_rounds`` params.
    """
    return {
        "tools": [chat_tool_definition(tool) for tool in tools] or None,
        "tool_providers": [provider.identifier for provider in tool_providers] or None,
        "execute_tools": None if execute_tools else False,
        "max_tool_rounds": max_tool_rounds if tools or tool_providers else None,
    }


def validate_passthrough_tool_conflicts(*, tools: Sequence[Tool], extra_body_parameters: Mapping[str, Any]) -> None:
    """
    Reject tools whose names a passthrough ``tools`` declaration also uses.

    Args:
        tools: The tools to advertise.
        extra_body_parameters: The target's passthrough request parameters.

    Raises:
        ValueError: If a tool name is also declared in ``extra_body_parameters["tools"]``.
    """
    if not tools:
        return
    declared = {definition.get("function", {}).get("name") for definition in extra_body_parameters.get("tools") or []}
    conflicts = {tool.name for tool in tools} & declared
    if conflicts:
        raise ValueError(f"Tools conflict with extra_body_parameters declarations: {sorted(conflicts)}")


def chat_tool_call_finder(conversation: Sequence[Message]) -> Callable[[Message], list[PendingToolCall]]:
    """
    Build the shared tool loop's call reader, rejecting calls whose IDs would corrupt history.

    Args:
        conversation: The conversation being sent, whose call IDs cannot be reused.

    Returns:
        A function returning a response's calls in order, or raising ``InvalidToolCallsError``.
    """
    seen_ids = {
        parse_function_call(piece).id
        for message in conversation
        for piece in message.message_pieces
        if piece.converted_value_data_type == "function_call"
    }

    def find_calls(reply: Message) -> list[PendingToolCall]:
        call_pieces = [piece for piece in reply.message_pieces if piece.converted_value_data_type == "function_call"]
        if not call_pieces:
            return []
        if any(piece.is_truncated or piece.response_error != "none" for piece in reply.message_pieces):
            raise InvalidToolCallsError("Truncated or failed tool calls were not executed.")
        try:
            calls = [parse_function_call(piece) for piece in call_pieces]
        except ValueError as exc:
            raise InvalidToolCallsError(f"Invalid tool calls: {exc}") from exc
        ids = [call.id for call in calls]
        if len(set(ids)) != len(ids) or seen_ids.intersection(ids):
            raise InvalidToolCallsError("Invalid tool calls: Duplicate or reused tool call ID.")
        seen_ids.update(ids)
        return [
            PendingToolCall(
                call_id=call.id,
                name=call.function.name if isinstance(call.function, FunctionCall) else None,
                arguments=call.function.arguments if isinstance(call.function, FunctionCall) else call.function,
                section=json.loads(piece.converted_value),
            )
            for call, piece in zip(calls, call_pieces, strict=True)
        ]

    return find_calls


async def send_with_tools_async(
    *,
    normalized_conversation: list[Message],
    tools: Sequence[Tool],
    tool_providers: Sequence[ToolProvider],
    execute_tools: bool,
    max_tool_rounds: int,
    validate_tools: Callable[[Sequence[Tool]], None],
    send_completion_async: SendCompletionAsync,
) -> list[Message]:
    """
    Send a conversation, running the model's calls to configured tools in the shared tool loop.

    Opens each provider's execution scope for the whole send, then collects the tools and checks them
    before the first request. Without tools, or when ``execute_tools`` is False, sends one completion,
    which advertises any tools and returns their calls without running them. Pass no tools or
    providers to send without any, as capability probes do.

    Args:
        normalized_conversation: The conversation to send, ending with the request.
        tools: The tools available without discovery.
        tool_providers: The providers whose tools are discovered within their execution scope.
        execute_tools: Whether returned calls run locally.
        max_tool_rounds: The most completions per send whose calls run locally.
        validate_tools: Raises for collected tools the target can't advertise.
        send_completion_async: Sends a conversation with the given tools and returns the one-message response.

    Returns:
        list[Message]: Every response and tool result for the normalizer to persist, in order.
    """
    async with AsyncExitStack() as stack:
        for provider in tool_providers:
            if isinstance(provider, _ScopedToolProvider):
                await stack.enter_async_context(provider.execution_scope_async())
        collected = await collect_tools_async(tools=tools, providers=tool_providers)
        validate_tools(collected)
        if not collected or not execute_tools:
            return await send_completion_async(normalized_conversation, collected)

        async def send_async(conversation: Sequence[Message]) -> Message:
            [reply] = await send_completion_async(list(conversation), collected)
            return reply

        async def resolve_tools_async() -> tuple[Sequence[Tool], Mapping[str, ToolFunction]]:
            return collected, {}

        return await run_tool_loop_async(
            conversation=normalized_conversation,
            request_piece=normalized_conversation[-1].message_pieces[0],
            send_async=send_async,
            find_calls=chat_tool_call_finder(normalized_conversation),
            dispatch_async=lambda call: dispatch_tool_call_async(call=call, resolve_tools_async=resolve_tools_async),
            max_tool_rounds=max_tool_rounds,
        )
