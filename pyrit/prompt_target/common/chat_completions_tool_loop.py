# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The bounded local tool loop for targets that speak the Chat Completions wire format."""

import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from typing import Any, ClassVar

from pyrit.models import Message, PromptDataType, ProviderTraceReference, ToolDefinition
from pyrit.models.messages.chat_message import FunctionCall
from pyrit.prompt_target.common.tool_call_history import (
    TOOL_CALL_INPUT_MODALITIES,
    parse_function_call,
    parse_function_tool_declarations,
)
from pyrit.prompt_target.common.tool_loop import (
    InvalidToolCallsError,
    PendingToolCall,
    ToolDispatchResult,
    ToolFunction,
    dispatch_tool_call_async,
    run_tool_loop_async,
)
from pyrit.prompt_target.common.tool_provider import Tool, ToolProvider, _ScopedToolProvider, collect_tools_async
from pyrit.prompt_target.common.utils import nested_tools


class _ChatCompletionsToolLoopMixin(ABC):
    """Run a Chat Completions target's configured tools locally, in the shared bounded tool loop."""

    _direct_tools: list[Tool]
    _tool_providers: list[ToolProvider]
    _execute_tools: bool
    _max_tool_rounds: int
    _suppress_tools: bool
    _extra_body_parameters: dict[str, Any] | None
    RUNS_TOOLS_LOCALLY: ClassVar[bool] = True

    def _init_tools(
        self,
        *,
        tools: Sequence[Tool] | None,
        tool_providers: Sequence[ToolProvider] | None,
        execute_tools: bool,
        max_tool_rounds: int,
        n: int | None,
        extra_body_parameters: Mapping[str, Any] | None,
    ) -> None:
        """
        Check and store the tool settings.

        Args:
            tools: Tools to advertise to the model and execute on the host.
            tool_providers: Providers whose tools are discovered within each send.
            execute_tools: Whether returned calls run locally.
            max_tool_rounds: The most completions per send whose calls run locally.
            n: The number of completions each request asks for, before passthrough overrides.
            extra_body_parameters: The passthrough request parameters.
        """
        _check_tool_settings(
            has_tools=bool(tools or tool_providers),
            execute_tools=execute_tools,
            max_tool_rounds=max_tool_rounds,
            n=n,
            extra_body_parameters=extra_body_parameters or {},
        )
        self._direct_tools = list(tools or [])
        self._tool_providers = list(tool_providers or [])
        self._execute_tools = execute_tools
        self._max_tool_rounds = max_tool_rounds
        self._suppress_tools = False

    def _configures_function_tools(self) -> bool:
        """
        Whether the request advertises function tools, without contacting any tool provider.

        Returns:
            bool: True for configured tools, a tool provider, or a function tool declared in
                ``extra_body_parameters["tools"]``. Tools a nested ``extra_body`` declares replace these.
        """
        replacing = nested_tools(self._extra_body_parameters)
        if replacing is not None:
            return any(tool.get("type") == "function" for tool in replacing)
        declared = (self._extra_body_parameters or {}).get("tools") or []
        return bool(
            self._direct_tools or self._tool_providers or any(tool.get("type") == "function" for tool in declared)
        )

    async def get_tool_definitions_async(self) -> list[ToolDefinition]:
        """
        Report the function tools advertised to the endpoint.

        Includes function declarations in ``extra_body_parameters["tools"]`` and the configured tools,
        discovering provider tools first. Function tools a nested ``extra_body`` declares replace these,
        since the request applies it last. No tool executes.

        Returns:
            list[ToolDefinition]: The advertised function tools.
        """
        replacing = nested_tools(self._extra_body_parameters)
        if replacing is not None:
            return parse_function_tool_declarations(replacing)
        declared = parse_function_tool_declarations((self._extra_body_parameters or {}).get("tools") or [])
        async with AsyncExitStack() as stack:
            for provider in self._tool_providers:
                if isinstance(provider, _ScopedToolProvider):
                    await stack.enter_async_context(provider.execution_scope_async())
            tools = await collect_tools_async(tools=self._direct_tools, providers=self._tool_providers)
        return [*declared, *(tool.to_definition() for tool in tools)]

    def _tool_identifier_params(self) -> dict[str, Any]:
        """
        Return the identifier params for the tool settings, all None for a target without tools.

        Returns:
            dict[str, Any]: The ``tools``, ``tool_providers``, ``execute_tools`` and ``max_tool_rounds`` params.
        """
        return {
            "tools": [_chat_tool_definition(tool) for tool in self._direct_tools] or None,
            "tool_providers": [provider.identifier for provider in self._tool_providers] or None,
            "execute_tools": None if self._execute_tools else False,
            "max_tool_rounds": self._max_tool_rounds if self._direct_tools or self._tool_providers else None,
        }

    def _implied_input_modalities(self) -> frozenset[frozenset[PromptDataType]]:
        """
        Accept tool-call history when tools run locally, since the target's own tool loop produces it.

        Advertised input modalities are unchanged, so callers can still tell whether the model is known to call tools.

        Returns:
            frozenset[frozenset[PromptDataType]]: Tool-call combinations, or none without configured tools.
        """
        return TOOL_CALL_INPUT_MODALITIES if self._direct_tools or self._tool_providers else frozenset()

    def _validate_tool_definitions(self, tools: Sequence[Tool]) -> None:
        """
        Reject tools whose names a passthrough ``tools`` declaration also uses.

        Args:
            tools: The tools to advertise.

        Raises:
            ValueError: If a tool name is also declared in ``extra_body_parameters["tools"]``.
        """
        if not tools:
            return
        declarations = (self._extra_body_parameters or {}).get("tools") or []
        conflicts = {tool.name for tool in tools} & {
            declaration.get("function", {}).get("name") for declaration in declarations
        }
        if conflicts:
            raise ValueError(f"Tools conflict with extra_body_parameters declarations: {sorted(conflicts)}")

    @staticmethod
    def _add_tool_definitions(*, body: dict[str, Any], tools: Sequence[Tool]) -> None:
        """
        Advertise tools in a request body, after any passthrough declarations.

        Args:
            body: The request body to update.
            tools: The tools to advertise.
        """
        if tools:
            body["tools"] = [*body.get("tools", []), *(_chat_tool_definition(tool) for tool in tools)]

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Send the conversation, running the model's calls to configured tools locally.

        Each provider's execution scope stays open for the whole send. Without tools, or when
        ``execute_tools`` is False, one completion is sent, which advertises any tools and returns
        their calls without running them. Capability probes suppress tools entirely.

        Args:
            normalized_conversation (list[Message]): The conversation to send, ending with the request.

        Returns:
            list[Message]: Every response and tool result for the normalizer to persist, in order.
        """
        providers = () if self._suppress_tools else self._tool_providers
        async with AsyncExitStack() as stack:
            for provider in providers:
                if isinstance(provider, _ScopedToolProvider):
                    await stack.enter_async_context(provider.execution_scope_async())
            tools = await collect_tools_async(
                tools=() if self._suppress_tools else self._direct_tools, providers=providers
            )
            self._validate_tool_definitions(tools)
            if tools and self._execute_tools:
                return await self._run_tool_loop_async(conversation=normalized_conversation, tools=tools)
            request = normalized_conversation[-1]
            return [
                await self._send_completion_async(conversation=normalized_conversation, request=request, tools=tools)
            ]

    async def _run_tool_loop_async(self, *, conversation: list[Message], tools: Sequence[Tool]) -> list[Message]:
        request = conversation[-1]
        tool_dispatched = False

        async def send_async(working: Sequence[Message]) -> Message:
            return await self._send_completion_async(conversation=list(working), request=request, tools=tools)

        async def resolve_tools_async() -> tuple[Sequence[Tool], Mapping[str, ToolFunction]]:
            return tools, {}

        async def dispatch_async(call: PendingToolCall) -> ToolDispatchResult:
            nonlocal tool_dispatched
            tool_dispatched = True
            return await dispatch_tool_call_async(call=call, resolve_tools_async=resolve_tools_async)

        try:
            return await run_tool_loop_async(
                conversation=conversation,
                request_piece=request.message_pieces[0],
                send_async=send_async,
                find_calls=_chat_tool_call_finder(conversation),
                dispatch_async=dispatch_async,
                max_tool_rounds=self._max_tool_rounds,
            )
        finally:
            if tool_dispatched:
                # A local tool runs outside the provider's trace, and each completion gets a trace of its own,
                # so no one provider response id can name this send. It stays unlinked.
                for piece in request.message_pieces:
                    ProviderTraceReference.clear_metadata(piece.prompt_metadata)

    @abstractmethod
    async def _send_completion_async(
        self, *, conversation: list[Message], request: Message, tools: Sequence[Tool]
    ) -> Message:
        """
        Send one completion request.

        Args:
            conversation: The conversation to send, ending with the latest message.
            request: The request this send answers.
            tools: The tools to advertise.

        Returns:
            Message: The model's response.
        """


def _check_tool_settings(
    *,
    has_tools: bool,
    execute_tools: bool,
    max_tool_rounds: int,
    n: int | None,
    extra_body_parameters: Mapping[str, Any],
) -> None:
    """
    Check tool settings, including a nested ``extra_body``, which the request applies last.

    Raises:
        ValueError: If ``max_tool_rounds`` is not a nonnegative integer, a nested ``extra_body``
            declares tools that would replace the configured ones, or tools run locally with ``n`` above 1.
    """
    if isinstance(max_tool_rounds, bool) or not isinstance(max_tool_rounds, int) or max_tool_rounds < 0:
        raise ValueError("max_tool_rounds must be a nonnegative integer.")
    nested = extra_body_parameters.get("extra_body")
    nested_parameters: Mapping[str, Any] = nested if isinstance(nested, Mapping) else {}
    if has_tools and "tools" in nested_parameters:
        raise ValueError(
            "Tools in extra_body_parameters['extra_body'] would replace the configured tools. "
            "Declare passthrough tools in extra_body_parameters['tools'] instead."
        )
    effective_n = nested_parameters.get("n", extra_body_parameters.get("n", n))
    if execute_tools and has_tools and effective_n not in (None, 1):
        raise ValueError("Automatic tool execution requires n=1.")


def _chat_tool_definition(tool: Tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "strict": tool.strict,
        },
    }


def _chat_tool_call_finder(conversation: Sequence[Message]) -> Callable[[Message], list[PendingToolCall]]:
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
