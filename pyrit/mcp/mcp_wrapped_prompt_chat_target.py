# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
MCP-wrapped prompt target.

Composes Model Context Protocol (MCP) tool use into any PyRIT chat target:
the wrapper connects to the configured MCP servers, presents their tools to
the model as part of the system context, and drives a bounded agentic loop in
which the model can discover and call tools before producing its final answer.

This follows the implementation direction discussed on PyRIT issue #1273:
composition/wrapping of an existing ``PromptTarget`` rather than per-target
modifications, so every chat target (LiteLLM, Azure ML, Hugging Face, ...)
gains MCP tool use without code changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from pyrit.mcp._client import MCPClientSession, MCPTool, MCPToolResult
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_configuration import TargetConfiguration

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.mcp.mcp_server_config import MCPServerConfig

logger = logging.getLogger(__name__)

_TOOL_CALL_PREFIX = "TOOL_CALL:"
_TOOL_RESULT_PREFIX = "TOOL_RESULT"
_TOOL_ERROR_PREFIX = "TOOL_ERROR"
_MAX_TOOL_RESULT_CHARS = 8_000


def _truncate(text: str, limit: int = _MAX_TOOL_RESULT_CHARS) -> str:
    """Return ``text``, truncated with an explicit marker when over ``limit``."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [truncated {len(text) - limit} characters]"


class MCPWrappedPromptChatTarget(PromptTarget):
    """
    Wraps any inner chat target with MCP tool use.

    The wrapper connects to the configured MCP servers, lists their tools, and
    injects a tool catalog plus a strict tool-call protocol into the system
    context of each conversation. When the model wants to use a tool it
    responds with a ``TOOL_CALL: {json}`` line; the wrapper executes the call
    (subject to the configured allowlists and caps), feeds the result back as a
    labeled user turn, and continues until the model produces a final answer
    without a tool call or the round budget is exhausted.

    Sessions are established lazily on first use and reused across sends;
    :meth:`cleanup_target_async` tears them down.

    Intermediate agentic rounds happen inside the wrapper and are audit-logged;
    only the final response is returned to the caller (and thus scored), keeping
    memory and reports focused on the conversation-level behavior.
    """

    _DEFAULT_SYSTEM_PROMPT_PREFIX: str = (
        "You have access to the following tools exposed over the Model Context Protocol (MCP):\n\n"
    )
    _DEFAULT_SYSTEM_PROMPT_INSTRUCTIONS: str = (
        "\n\nTo call a tool, respond with exactly one line in this format and nothing else:\n"
        'TOOL_CALL: {"tool": "<tool name>", "arguments": {<json arguments>}}\n'
        "You will then receive the tool result and may call further tools. "
        "When you can answer without another tool call, respond with your final answer "
        "and do NOT include a TOOL_CALL line."
    )

    def __init__(
        self,
        *,
        inner_target: PromptTarget,
        mcp_servers: Sequence[MCPServerConfig],
        max_tool_call_rounds: int = 5,
        allowed_tools: Sequence[str] | None = None,
        system_prompt_prefix: str | None = None,
        system_prompt_instructions: str | None = None,
        custom_configuration: TargetConfiguration | None = None,
    ) -> None:
        """
        Initialize the wrapper.

        Args:
            inner_target (PromptTarget): The target to wrap. Must support system
                prompts and multi-turn conversations; the wrapper advertises the
                inner target's capabilities.
            mcp_servers (Sequence[MCPServerConfig]): MCP servers to connect to.
                Must be non-empty.
            max_tool_call_rounds (int): Maximum number of tool-call rounds per
                user prompt. Defaults to 5.
            allowed_tools (Sequence[str] | None): Global tool-name allowlist
                applied on top of each server's own ``allowed_tools``. ``None``
                (default) defers to the per-server allowlists.
            system_prompt_prefix (str | None): Replacement for the default catalog
                preamble, if callers want custom framing.
            system_prompt_instructions (str | None): Replacement for the default
                tool-call protocol instructions.
            custom_configuration (TargetConfiguration | None): Override the
                default (inner-target-derived) configuration.

        Raises:
            ValueError: If no MCP servers are configured, a server config is
                invalid, or the inner target lacks required capabilities.
        """
        if not mcp_servers:
            raise ValueError("MCPWrappedPromptChatTarget requires at least one MCP server.")
        inner_capabilities = inner_target.capabilities
        if not inner_capabilities.supports_system_prompt:
            raise ValueError(
                f"The inner target {type(inner_target).__name__} must support system prompts "
                "to receive the MCP tool catalog."
            )
        if not inner_capabilities.supports_multi_turn:
            raise ValueError(
                f"The inner target {type(inner_target).__name__} must support multi-turn conversations "
                "for the MCP tool-call loop."
            )

        self._servers: list[MCPServerConfig] = list(mcp_servers)
        for server in self._servers:
            server.validate_connection()
        self._servers_by_name: dict[str, MCPServerConfig] = {server.name: server for server in self._servers}
        self._max_tool_call_rounds = max(1, max_tool_call_rounds)
        self._global_allowed_tools: frozenset[str] | None = (
            frozenset(allowed_tools) if allowed_tools is not None else None
        )
        self._system_prompt_prefix = (
            system_prompt_prefix if system_prompt_prefix is not None else self._DEFAULT_SYSTEM_PROMPT_PREFIX
        )
        self._system_prompt_instructions = (
            system_prompt_instructions
            if system_prompt_instructions is not None
            else self._DEFAULT_SYSTEM_PROMPT_INSTRUCTIONS
        )
        self._inner_target = inner_target
        self._session_stack: AsyncExitStack | None = None
        self._sessions: dict[str, MCPClientSession] = {}
        self._sessions_lock = asyncio.Lock()

        super().__init__(
            custom_configuration=custom_configuration
            if custom_configuration is not None
            else TargetConfiguration(capabilities=inner_capabilities)
        )

    async def _get_or_connect_sessions_async(self) -> dict[str, MCPClientSession]:
        """
        Return the live sessions, connecting lazily on first use.

        Returns:
            dict[str, MCPClientSession]: Live sessions keyed by server name.
        """
        async with self._sessions_lock:
            if self._session_stack is None:
                stack = AsyncExitStack()
                try:
                    sessions: dict[str, MCPClientSession] = {}
                    for server in self._servers:
                        session = await stack.enter_async_context(MCPClientSession(config=server))
                        sessions[server.name] = session
                except BaseException:
                    await stack.aclose()
                    raise
                self._session_stack = stack
                self._sessions = sessions
                logger.info("Connected to %d MCP server(s): %s", len(sessions), ", ".join(sessions))
            return self._sessions

    async def cleanup_target_async(self) -> None:
        """Close MCP sessions. Safe to call multiple times."""
        async with self._sessions_lock:
            if self._session_stack is not None:
                await self._session_stack.aclose()
                self._session_stack = None
                self._sessions = {}

    def _tool_is_allowed(self, *, server: MCPServerConfig, tool_name: str) -> bool:
        """Return True when ``tool_name`` passes both the server and global allowlists."""
        if server.allowed_tools is not None and tool_name not in server.allowed_tools:
            return False
        return self._global_allowed_tools is None or tool_name in self._global_allowed_tools

    def _build_system_prompt(self, *, catalog: Mapping[str, Sequence[MCPTool]]) -> str:
        """
        Build the system context: tool catalog plus the tool-call protocol.

        Args:
            catalog (Mapping[str, Sequence[MCPTool]]): Declared tools per server name.

        Returns:
            str: The full system prompt to inject.
        """
        lines: list[str] = []
        for server_name, tools in catalog.items():
            server = self._servers_by_name[server_name]
            for tool in tools:
                if not self._tool_is_allowed(server=server, tool_name=tool.name):
                    continue
                schema = json.dumps(tool.input_schema, sort_keys=True) if tool.input_schema else "{}"
                lines.append(
                    f"- {server_name}/{tool.name}: {tool.description or 'No description.'} | parameters: {schema}"
                )
        body = "\n".join(lines) if lines else "(no tools available)"
        return self._system_prompt_prefix + body + self._system_prompt_instructions

    def _build_system_message(self, *, conversation_id: str, sequence: int, text: str) -> Message:
        """
        Build the synthetic system message carrying the tool catalog.

        Returns:
            Message: The system-role message to prepend.
        """
        return Message(
            message_pieces=[
                MessagePiece(
                    role="system",
                    original_value=text,
                    converted_value=text,
                    conversation_id=conversation_id,
                    sequence=sequence,
                    prompt_metadata={"mcp_system_context": True},
                )
            ]
        )

    def _build_tool_result_message(
        self,
        *,
        conversation_id: str,
        sequence: int,
        server_name: str,
        tool_name: str,
        result: MCPToolResult,
    ) -> Message:
        """
        Build the labeled user turn that feeds a tool result back to the model.

        Returns:
            Message: The user-role message carrying the (possibly truncated) result.
        """
        prefix = _TOOL_ERROR_PREFIX if result.is_error else _TOOL_RESULT_PREFIX
        body = f"{prefix}[{server_name}/{tool_name}]: {_truncate(result.text)}"
        return Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value=body,
                    converted_value=body,
                    conversation_id=conversation_id,
                    sequence=sequence,
                    prompt_metadata={"mcp_server": server_name, "mcp_tool": tool_name, "mcp_is_error": result.is_error},
                )
            ]
        )

    def _build_tool_call_error_message(self, *, conversation_id: str, sequence: int, error: str) -> Message:
        """
        Build the user turn that bounces an invalid or denied tool call back to the model.

        Returns:
            Message: The user-role message describing the rejection.
        """
        body = f"{_TOOL_ERROR_PREFIX}: {error} Respond with your final answer or a corrected TOOL_CALL line."
        return Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value=body,
                    converted_value=body,
                    conversation_id=conversation_id,
                    sequence=sequence,
                    prompt_metadata={"mcp_call_error": error},
                )
            ]
        )

    @staticmethod
    def _parse_tool_call(text: str) -> dict[str, Any] | None:
        """
        Extract the last valid ``TOOL_CALL:`` line from an assistant response.

        Args:
            text (str): The assistant response text.

        Returns:
            dict[str, Any] | None: ``{"tool": ..., "arguments": ...}`` when a
                well-formed call is present; ``None`` otherwise. Malformed call
                lines are treated as absent so the model can correct itself on
                the next round.
        """
        if _TOOL_CALL_PREFIX not in text:
            return None
        call: dict[str, Any] | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(_TOOL_CALL_PREFIX):
                continue
            payload = stripped[len(_TOOL_CALL_PREFIX) :].strip()
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                call = None
                continue
            if (
                isinstance(parsed, dict)
                and isinstance(parsed.get("tool"), str)
                and isinstance(parsed.get("arguments"), dict)
            ):
                call = parsed
            else:
                call = None
        return call

    @staticmethod
    def _response_text(messages: Sequence[Message]) -> str:
        """
        Concatenate the assistant text of the last response message.

        Returns:
            str: The assistant text, or an empty string when there is none.
        """
        if not messages:
            return ""
        return "\n".join(
            piece.converted_value or piece.original_value
            for piece in messages[-1].message_pieces
            if piece.api_role == "assistant"
        )

    @staticmethod
    def _next_sequence(conversation: Sequence[Message]) -> int:
        """Return the next sequence number after the highest in the conversation."""
        return (
            max(
                (piece.sequence for message in conversation for piece in message.message_pieces),
                default=-1,
            )
            + 1
        )

    @staticmethod
    def _conversation_id(conversation: Sequence[Message]) -> str:
        """Return the conversation id shared by the conversation's messages."""
        return conversation[-1].message_pieces[0].conversation_id or ""

    def _find_tool(
        self,
        *,
        sessions: Mapping[str, MCPClientSession],
        catalog: Mapping[str, Sequence[MCPTool]],
        tool_name: str,
    ) -> tuple[MCPClientSession, str, MCPTool] | None:
        """
        Resolve a catalog tool name (``server/tool`` or bare ``tool``) to a session.

        Args:
            sessions (Mapping[str, MCPClientSession]): Live sessions by server name.
            catalog (Mapping[str, Sequence[MCPTool]]): Declared tools per server name.
            tool_name (str): The name the model used in its TOOL_CALL.

        Returns:
            tuple[MCPClientSession, str, MCPTool] | None: Session, canonical
                ``server/tool`` name, and tool; ``None`` when no unique match exists.
        """
        if "/" in tool_name:
            server_name, _, bare_name = tool_name.partition("/")
            session = sessions.get(server_name)
            if session is None:
                return None
            for tool in catalog.get(server_name, []):
                if tool.name == bare_name:
                    return session, f"{server_name}/{bare_name}", tool
            return None
        matches = [
            (sessions[server_name], f"{server_name}/{tool.name}", tool)
            for server_name, tools in catalog.items()
            for tool in tools
            if tool.name == tool_name
        ]
        return matches[0] if len(matches) == 1 else None

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Run the bounded agentic MCP loop against the inner target.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after the wrapper's normalization
                pipeline.

        Returns:
            list[Message]: The inner target's final response messages. When the
                round budget is exhausted mid-loop, the last response is returned
                as-is (it may still contain a ``TOOL_CALL`` line) and a warning
                is logged.
        """
        sessions = await self._get_or_connect_sessions_async()
        catalog: dict[str, list[MCPTool]] = {name: await session.list_tools() for name, session in sessions.items()}

        conversation: list[Message] = list(normalized_conversation)
        conversation_id = self._conversation_id(conversation)
        sequence = self._next_sequence(conversation)
        conversation.insert(
            0,
            self._build_system_message(
                conversation_id=conversation_id,
                sequence=sequence,
                text=self._build_system_prompt(catalog=catalog),
            ),
        )
        sequence += 1

        response: list[Message] = []
        for _ in range(self._max_tool_call_rounds):
            response = await self._inner_target._send_prompt_to_target_async(normalized_conversation=conversation)
            assistant_text = self._response_text(response)
            call = self._parse_tool_call(assistant_text)

            if call is None:
                return response

            tool_name = call["tool"]
            resolved = self._find_tool(sessions=sessions, catalog=catalog, tool_name=tool_name)
            if resolved is None:
                logger.warning("[MCP audit] denied unknown tool '%s'", tool_name)
                conversation.append(response[-1])
                conversation.append(
                    self._build_tool_call_error_message(
                        conversation_id=conversation_id,
                        sequence=sequence,
                        error=f"Unknown tool '{tool_name}'.",
                    )
                )
                sequence += 1
                continue

            session, canonical_name, tool = resolved
            server_name = canonical_name.split("/", 1)[0]
            server = self._servers_by_name[server_name]
            if not self._tool_is_allowed(server=server, tool_name=tool.name):
                logger.warning("[MCP audit] denied tool '%s' by allowlist", canonical_name)
                conversation.append(response[-1])
                conversation.append(
                    self._build_tool_call_error_message(
                        conversation_id=conversation_id,
                        sequence=sequence,
                        error=f"Tool '{canonical_name}' is not allowed by policy.",
                    )
                )
                sequence += 1
                continue

            logger.info("[MCP audit] calling %s with arguments: %s", canonical_name, call["arguments"])
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name=tool.name, arguments=call["arguments"]),
                    timeout=server.tool_call_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "[MCP audit] tool call '%s' timed out after %ss", canonical_name, server.tool_call_timeout
                )
                result = MCPToolResult(text="Tool call timed out.", is_error=True)
            logger.info(
                "[MCP audit] tool call '%s' finished (is_error=%s, %d chars)",
                canonical_name,
                result.is_error,
                len(result.text),
            )

            conversation.append(response[-1])
            conversation.append(
                self._build_tool_result_message(
                    conversation_id=conversation_id,
                    sequence=sequence,
                    server_name=server_name,
                    tool_name=tool.name,
                    result=result,
                )
            )
            sequence += 1

        logger.warning(
            "MCP tool-call loop hit the round cap (%d); returning the last response.", self._max_tool_call_rounds
        )
        return response
