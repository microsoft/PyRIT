# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the MCP-wrapped prompt target's agentic loop protocol."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.mcp._client import MCPTool, MCPToolResult
from pyrit.mcp.mcp_server_config import MCPServerConfig, MCPTransport
from pyrit.mcp.mcp_wrapped_prompt_chat_target import MCPWrappedPromptChatTarget
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.target_configuration import TargetCapabilities


def _conversation(*, user_text: str = "What is 2 + 3?") -> list[Message]:
    return [
        Message(
            message_pieces=[MessagePiece(role="user", original_value=user_text, converted_value=user_text, sequence=0)]
        )
    ]


def _assistant_message(text: str) -> list[Message]:
    return [Message(message_pieces=[MessagePiece(role="assistant", original_value=text, converted_value=text)])]


def _capabilities(*, system_prompt: bool = True, multi_turn: bool = True) -> TargetCapabilities:
    return TargetCapabilities(supports_system_prompt=system_prompt, supports_multi_turn=multi_turn)


def _inner_target(responses: list[str], *, capabilities: TargetCapabilities | None = None) -> MagicMock:
    """Build an inner target stub returning the given assistant texts in order."""
    inner = MagicMock()
    inner.capabilities = capabilities if capabilities is not None else _capabilities()
    inner._send_prompt_to_target_async = AsyncMock(side_effect=[_assistant_message(text) for text in responses])
    return inner


def _fake_session(*, tools: list[MCPTool], call_results: list[MCPToolResult]) -> MagicMock:
    """Build a session stub whose list_tools/call_tool behave like the real client."""
    session = MagicMock()
    session.list_tools = AsyncMock(return_value=tools)
    session.call_tool = AsyncMock(side_effect=call_results)
    return session


def _wrapped_target(
    inner: MagicMock,
    *,
    sessions: dict[str, MagicMock] | None = None,
    server_allowed_tools: list[str] | None = None,
    global_allowed_tools: list[str] | None = None,
    max_tool_call_rounds: int = 5,
) -> MCPWrappedPromptChatTarget:
    config = MCPServerConfig(
        name="calc",
        transport=MCPTransport.STDIO,
        command="python",
        args=["server.py"],
        allowed_tools=server_allowed_tools,
    )
    wrapper = MCPWrappedPromptChatTarget(
        inner_target=inner,
        mcp_servers=[config],
        max_tool_call_rounds=max_tool_call_rounds,
        allowed_tools=global_allowed_tools,
    )

    if sessions is None:
        sessions = {
            "calc": _fake_session(
                tools=[
                    MCPTool(name="add", description="Add two numbers.", input_schema={"type": "object"}),
                    MCPTool(name="echo", description="Echo text.", input_schema={"type": "object"}),
                ],
                call_results=[MCPToolResult(text="42", is_error=False) for _ in range(32)],
            )
        }

    async def _sessions():
        return sessions

    wrapper._get_or_connect_sessions_async = _sessions  # type: ignore[method-assign]
    return wrapper


@pytest.mark.usefixtures("patch_central_database")
class TestWrapperInitialization:
    def test_requires_inner_system_prompt_support(self):
        inner = _inner_target(["x"], capabilities=_capabilities(system_prompt=False))
        with pytest.raises(ValueError, match="system prompts"):
            MCPWrappedPromptChatTarget(
                inner_target=inner,
                mcp_servers=[MCPServerConfig(name="s", transport=MCPTransport.STDIO, command="python")],
            )

    def test_requires_inner_multi_turn_support(self):
        inner = _inner_target(["x"], capabilities=_capabilities(multi_turn=False))
        with pytest.raises(ValueError, match="multi-turn"):
            MCPWrappedPromptChatTarget(
                inner_target=inner,
                mcp_servers=[MCPServerConfig(name="s", transport=MCPTransport.STDIO, command="python")],
            )

    def test_requires_at_least_one_server(self):
        inner = _inner_target(["x"])
        with pytest.raises(ValueError, match="at least one MCP server"):
            MCPWrappedPromptChatTarget(inner_target=inner, mcp_servers=[])

    def test_invalid_server_connection_params_rejected(self):
        inner = _inner_target(["x"])
        with pytest.raises(ValueError, match="stdio transport requires"):
            MCPWrappedPromptChatTarget(
                inner_target=inner,
                mcp_servers=[MCPServerConfig(name="s", transport=MCPTransport.STDIO)],
            )

    def test_inner_capabilities_are_advertised(self):
        inner = _inner_target(["x"])
        wrapper = _wrapped_target(inner)
        assert wrapper.capabilities.supports_system_prompt is True
        assert wrapper.capabilities.supports_multi_turn is True


class TestToolCallParsing:
    def test_parses_last_valid_call(self):
        text = 'thinking...\nTOOL_CALL: {"tool": "add", "arguments": {"a": 2, "b": 3}}'
        assert MCPWrappedPromptChatTarget._parse_tool_call(text) == {"tool": "add", "arguments": {"a": 2, "b": 3}}

    def test_returns_none_without_call(self):
        assert MCPWrappedPromptChatTarget._parse_tool_call("final answer: 5") is None

    def test_malformed_json_is_rejected(self):
        assert MCPWrappedPromptChatTarget._parse_tool_call('TOOL_CALL: {"tool": ') is None

    def test_missing_arguments_is_rejected(self):
        assert MCPWrappedPromptChatTarget._parse_tool_call('TOOL_CALL: {"tool": "add"}') is None


@pytest.mark.usefixtures("patch_central_database")
class TestAgenticLoop:
    async def test_final_answer_without_tool_call(self):
        inner = _inner_target(["The answer is 5."])
        wrapper = _wrapped_target(inner)

        response = await wrapper._send_prompt_to_target_async(normalized_conversation=_conversation())

        assert response[-1].message_pieces[0].original_value == "The answer is 5."
        assert inner._send_prompt_to_target_async.await_count == 1

    async def test_system_context_injected_once_with_catalog(self):
        inner = _inner_target(["final"])
        wrapper = _wrapped_target(inner)

        await wrapper._send_prompt_to_target_async(normalized_conversation=_conversation())

        first_conversation = inner._send_prompt_to_target_async.await_args_list[0].kwargs["normalized_conversation"]
        system_pieces = [piece for m in first_conversation for piece in m.message_pieces if piece.role == "system"]
        assert len(system_pieces) == 1
        assert "calc/add" in system_pieces[0].original_value
        assert "TOOL_CALL:" in system_pieces[0].original_value
        # The original user message is preserved after the injected system context.
        assert first_conversation[1].message_pieces[0].original_value == "What is 2 + 3?"

    async def test_tool_call_executes_and_result_fed_back(self):
        inner = _inner_target(
            [
                'TOOL_CALL: {"tool": "add", "arguments": {"a": 2, "b": 3}}',
                "The answer is 5.",
            ]
        )
        session = _fake_session(
            tools=[MCPTool(name="add", description="Add two numbers.", input_schema={"type": "object"})],
            call_results=[MCPToolResult(text="5", is_error=False)],
        )
        wrapper = _wrapped_target(inner, sessions={"calc": session})

        response = await wrapper._send_prompt_to_target_async(normalized_conversation=_conversation())

        assert response[-1].message_pieces[0].original_value == "The answer is 5."
        assert inner._send_prompt_to_target_async.await_count == 2
        assert session.call_tool.await_count == 1
        assert session.call_tool.await_args.kwargs == {"tool_name": "add", "arguments": {"a": 2, "b": 3}}
        second_conversation = inner._send_prompt_to_target_async.await_args_list[1].kwargs["normalized_conversation"]
        tool_result_texts = [
            piece.original_value
            for m in second_conversation
            for piece in m.message_pieces
            if piece.original_value.startswith("TOOL_RESULT")
        ]
        assert tool_result_texts == ["TOOL_RESULT[calc/add]: 5"]

    async def test_unknown_tool_is_denied_not_executed(self):
        inner = _inner_target(
            [
                'TOOL_CALL: {"tool": "does_not_exist", "arguments": {}}',
                "Final answer.",
            ]
        )
        wrapper = _wrapped_target(inner)

        response = await wrapper._send_prompt_to_target_async(normalized_conversation=_conversation())

        assert response[-1].message_pieces[0].original_value == "Final answer."
        second_conversation = inner._send_prompt_to_target_async.await_args_list[1].kwargs["normalized_conversation"]
        denial = [
            piece for m in second_conversation for piece in m.message_pieces if "Unknown tool" in piece.original_value
        ]
        assert len(denial) == 1

    async def test_disallowed_tool_is_denied(self):
        inner = _inner_target(
            [
                'TOOL_CALL: {"tool": "echo", "arguments": {"text": "x"}}',
                "Final answer.",
            ]
        )
        wrapper = _wrapped_target(inner, server_allowed_tools=["add"])

        response = await wrapper._send_prompt_to_target_async(normalized_conversation=_conversation())

        assert response[-1].message_pieces[0].original_value == "Final answer."
        second_conversation = inner._send_prompt_to_target_async.await_args_list[1].kwargs["normalized_conversation"]
        denial = [
            piece
            for m in second_conversation
            for piece in m.message_pieces
            if "not allowed by policy" in piece.original_value
        ]
        assert len(denial) == 1

    async def test_round_cap_returns_last_response(self):
        inner = _inner_target(['TOOL_CALL: {"tool": "add", "arguments": {"a": 1, "b": 2}}'] * 3)
        wrapper = _wrapped_target(inner, max_tool_call_rounds=2)

        response = await wrapper._send_prompt_to_target_async(normalized_conversation=_conversation())

        assert inner._send_prompt_to_target_async.await_count == 2
        assert "TOOL_CALL" in response[-1].message_pieces[0].original_value

    async def test_cleanup_closes_sessions(self):
        inner = _inner_target(["final"])
        wrapper = _wrapped_target(inner)

        await wrapper._get_or_connect_sessions_async()
        await wrapper.cleanup_target_async()

        sessions = await wrapper._get_or_connect_sessions_async()
        assert sessions  # reconnected lazily after cleanup
        await wrapper.cleanup_target_async()
