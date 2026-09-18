# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import inspect
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from mcp import StdioServerParameters
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import ValidationError

from pyrit.models import JsonResponseConfig, Message, MessagePiece
from pyrit.prompt_target import (
    FunctionTool,
    MCPConfig,
    MCPStdioServerConfig,
    MCPStreamableHTTPServerConfig,
    MCPToolProvider,
    OpenAIResponseTarget,
    ToolProvider,
)


def _write_config(path: Path, server: dict[str, object]) -> None:
    path.write_text(json.dumps({"servers": {"notes": server}}), encoding="utf-8")


def _http_provider() -> MCPToolProvider:
    return MCPToolProvider(
        server_name="notes",
        server_config=MCPStreamableHTTPServerConfig(url="http://127.0.0.1:8000/mcp/notes"),
    )


def test_from_config_file_loads_streamable_http_server(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp.json"
    _write_config(
        config_path,
        {
            "type": "http",
            "url": "http://127.0.0.1:8000/mcp/notes",
            "headers": {"Authorization": "Bearer test"},
        },
    )

    provider = MCPToolProvider.from_config_file(config_path=config_path, server_name="notes")

    assert provider.server_name == "notes"
    assert provider.server_url == "http://127.0.0.1:8000/mcp/notes"
    assert isinstance(provider.server_config, MCPStreamableHTTPServerConfig)
    assert provider.identifier == {
        "type": "MCPToolProvider",
        "server_name": "notes",
        "transport": "http",
    }


def test_from_config_loads_inline_server() -> None:
    provider = MCPToolProvider.from_config(
        config={
            "servers": {
                "notes": {
                    "type": "http",
                    "url": "http://127.0.0.1:8000/mcp/notes",
                }
            }
        },
        server_name="notes",
    )

    assert provider.server_name == "notes"
    assert provider.server_url == "http://127.0.0.1:8000/mcp/notes"


@pytest.mark.parametrize(
    ("server", "match"),
    [
        ({"type": "stdio", "command": ""}, "at least 1 character"),
        ({"type": "http"}, "url"),
        ({"type": "http", "url": "file:///tmp/mcp"}, "must start with http"),
    ],
)
def test_from_config_file_rejects_unsupported_server(tmp_path: Path, server: dict[str, object], match: str) -> None:
    config_path = tmp_path / "mcp.json"
    _write_config(config_path, server)

    with pytest.raises((ValueError, ValidationError), match=match):
        MCPToolProvider.from_config_file(config_path=config_path, server_name="notes")


def test_from_config_loads_stdio_server_and_infers_legacy_transport() -> None:
    provider = MCPToolProvider.from_config(
        config={
            "mcpServers": {
                "notes": {
                    "command": "python",
                    "args": ["notes_server.py"],
                    "env": {"MODE": "test"},
                    "cwd": "tools",
                }
            }
        },
        server_name="notes",
    )

    assert provider.server_url is None
    assert provider.server_config == MCPStdioServerConfig(
        command="python",
        args=["notes_server.py"],
        env={"MODE": "test"},
        cwd="tools",
    )
    assert provider.identifier == {
        "type": "MCPToolProvider",
        "server_name": "notes",
        "transport": "stdio",
    }


def test_from_config_accepts_validated_config() -> None:
    config = MCPConfig(
        servers={
            "notes": MCPStreamableHTTPServerConfig(url="http://127.0.0.1:8000/mcp/notes"),
        }
    )

    provider = MCPToolProvider.from_config(config=config, server_name="notes")

    assert provider.server_config is config.servers["notes"]


async def test_create_session_async_uses_stdio_transport() -> None:
    provider = MCPToolProvider(
        server_name="notes",
        server_config=MCPStdioServerConfig(
            command="python",
            args=["notes_server.py"],
            env={"MODE": "test"},
            cwd="tools",
        ),
    )
    streams = (MagicMock(), MagicMock())
    stdio_parameters: StdioServerParameters | None = None

    @asynccontextmanager
    async def fake_stdio_client(parameters: StdioServerParameters) -> AsyncIterator[tuple[MagicMock, MagicMock]]:
        nonlocal stdio_parameters
        stdio_parameters = parameters
        yield streams

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.initialize = AsyncMock()
    client_session = MagicMock(return_value=session)

    with (
        patch("pyrit.prompt_target.common.mcp_tool_provider.stdio_client", fake_stdio_client),
        patch("pyrit.prompt_target.common.mcp_tool_provider.ClientSession", client_session),
    ):
        async with provider._create_session_async() as created_session:
            assert created_session is session

    assert stdio_parameters is not None
    assert stdio_parameters.command == "python"
    assert stdio_parameters.args == ["notes_server.py"]
    assert stdio_parameters.env == {"MODE": "test"}
    assert stdio_parameters.cwd == Path("tools")
    client_session.assert_called_once_with(*streams)
    session.initialize.assert_awaited_once()


async def test_create_session_async_uses_mcp_http_timeouts() -> None:
    provider = MCPToolProvider(
        server_name="notes",
        server_config=MCPStreamableHTTPServerConfig(
            url="http://127.0.0.1:8000/mcp/notes",
            headers={"Authorization": "Bearer token"},
        ),
    )
    streams = (MagicMock(), MagicMock(), MagicMock())
    expected_http_client = MagicMock()
    http_client_context = MagicMock()
    http_client_context.__aenter__ = AsyncMock(return_value=expected_http_client)
    http_client_context.__aexit__ = AsyncMock(return_value=None)
    async_client = MagicMock(return_value=http_client_context)

    @asynccontextmanager
    async def fake_streamable_http_client_async(url: str, *, http_client: MagicMock):
        assert url == "http://127.0.0.1:8000/mcp/notes"
        assert http_client is expected_http_client
        yield streams

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.initialize = AsyncMock()

    with (
        patch("pyrit.prompt_target.common.mcp_tool_provider.httpx.AsyncClient", async_client),
        patch(
            "pyrit.prompt_target.common.mcp_tool_provider.streamable_http_client",
            fake_streamable_http_client_async,
        ),
        patch("pyrit.prompt_target.common.mcp_tool_provider.ClientSession", MagicMock(return_value=session)),
    ):
        async with provider._create_session_async():
            pass

    timeout = async_client.call_args.kwargs["timeout"]
    assert timeout == httpx.Timeout(30.0, read=300.0)


async def test_get_tools_async_discovers_all_pages() -> None:
    provider = _http_provider()
    session = MagicMock()
    session.list_tools = AsyncMock(
        side_effect=[
            ListToolsResult(
                tools=[
                    Tool(
                        name="get_note",
                        description="Read a note",
                        inputSchema={
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        },
                    )
                ],
                nextCursor="page-2",
            ),
            ListToolsResult(
                tools=[
                    Tool(
                        name="list_notes",
                        description="List notes",
                        inputSchema={"type": "object", "properties": {}},
                    )
                ]
            ),
        ]
    )

    @asynccontextmanager
    async def create_session():
        yield session

    provider._create_session_async = create_session  # type: ignore[method-assign]

    tools = await provider.get_tools_async()
    cached_tools = await provider.get_tools_async()

    assert [tool.name for tool in tools] == ["get_note", "list_notes"]
    assert tools[0].description == "Read a note"
    assert tools[0].parameters == {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }
    assert [tool.name for tool in cached_tools] == ["get_note", "list_notes"]
    assert session.list_tools.call_count == 2


async def test_call_tool_async_returns_mcp_result() -> None:
    provider = _http_provider()
    provider._tools = [
        Tool(
            name="get_note",
            description="Read a note",
            inputSchema={"type": "object", "properties": {}},
        )
    ]
    session = MagicMock()
    session.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text='{"text":"Welcome"}')],
            structuredContent={"text": "Welcome"},
        )
    )

    @asynccontextmanager
    async def create_session():
        yield session

    provider._create_session_async = create_session  # type: ignore[method-assign]

    tools = await provider.get_tools_async()
    result = await tools[0].execute_async(arguments={"id": "welcome"})

    assert result == {
        "content": [{"type": "text", "text": '{"text":"Welcome"}'}],
        "is_error": False,
        "structured_content": {"text": "Welcome"},
    }
    session.call_tool.assert_awaited_once_with(name="get_note", arguments={"id": "welcome"})


async def test_provider_reuses_session_within_execution_scope() -> None:
    provider = _http_provider()
    session = MagicMock()
    session.list_tools = AsyncMock(
        return_value=ListToolsResult(
            tools=[
                Tool(
                    name="get_note",
                    description="Read a note",
                    inputSchema={"type": "object", "properties": {}},
                )
            ]
        )
    )
    session.call_tool = AsyncMock(return_value=CallToolResult(content=[]))
    lifecycle: list[str] = []

    @asynccontextmanager
    async def create_session():
        lifecycle.append("enter")
        try:
            yield session
        finally:
            lifecycle.append("exit")

    provider._create_session_async = create_session  # type: ignore[method-assign]

    async with provider.execution_scope_async():
        tools = await provider.get_tools_async()
        await tools[0].execute_async(arguments={})
        await tools[0].execute_async(arguments={})

    assert session.call_tool.await_count == 2
    assert lifecycle == ["enter", "exit"]


async def test_openai_response_target_scopes_provider_session_to_send() -> None:
    provider = MagicMock(spec=MCPToolProvider)
    lifecycle: list[str] = []

    @asynccontextmanager
    async def execution_scope():
        lifecycle.append("enter")
        try:
            yield
        finally:
            lifecycle.append("exit")

    provider.execution_scope_async = execution_scope
    target = object.__new__(OpenAIResponseTarget)
    target._tool_providers = [provider]
    target._run_tool_call_loop_async = AsyncMock(return_value=[])  # type: ignore[method-assign]
    send_implementation = inspect.unwrap(OpenAIResponseTarget._send_prompt_to_target_async)

    result = await send_implementation(target, normalized_conversation=[])

    assert result == []
    assert lifecycle == ["enter", "exit"]
    target._run_tool_call_loop_async.assert_awaited_once_with(normalized_conversation=[])


def test_openai_response_target_finds_parallel_function_calls() -> None:
    target = object.__new__(OpenAIResponseTarget)
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value='{"type":"function_call","call_id":"call-1","name":"get_note","arguments":"{}"}',
                original_value_data_type="function_call",
            ),
            MessagePiece(
                role="assistant",
                original_value='{"type":"function_call","call_id":"call-2","name":"list_notes","arguments":"{}"}',
                original_value_data_type="function_call",
            ),
        ]
    )

    calls = target._find_pending_tool_calls(response)

    assert [call["call_id"] for call in calls] == ["call-1", "call-2"]


async def test_openai_response_target_uses_mcp_tools(patch_central_database) -> None:
    provider = _http_provider()
    provider._tools = [
        Tool(
            name="get_note",
            description="Read a note",
            inputSchema={"type": "object", "properties": {"id": {"type": "string"}}},
        )
    ]
    provider.call_tool_async = AsyncMock(return_value={"structured_content": {"text": "Welcome"}})  # type: ignore[method-assign]
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tool_providers=[provider],
    )
    conversation = [Message.from_prompt(prompt="Read the welcome note", role="user")]

    first_body = await target._construct_request_body_async(
        conversation=conversation,
        json_config=JsonResponseConfig(enabled=False),
    )
    second_body = await target._construct_request_body_async(
        conversation=conversation,
        json_config=JsonResponseConfig(enabled=False),
    )
    output = await target._execute_call_section_async(
        {
            "type": "function_call",
            "name": "get_note",
            "arguments": '{"id":"welcome"}',
            "call_id": "call-1",
        }
    )

    assert first_body["tools"] == second_body["tools"]
    assert first_body["tools"][0]["name"] == "get_note"
    provider.call_tool_async.assert_awaited_once_with(name="get_note", arguments={"id": "welcome"})
    assert output == {"structured_content": {"text": "Welcome"}}


async def test_openai_response_target_rejects_provider_name_conflict(patch_central_database) -> None:
    async def get_note_async() -> dict[str, bool]:
        return {"ok": True}

    configured_tool = FunctionTool(function=get_note_async, name="get_note")
    provider = MagicMock(spec=ToolProvider)
    provider.identifier = {"type": "test"}
    provider.get_tools_async = AsyncMock(return_value=[configured_tool])
    target = OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
        tools=[configured_tool],
        tool_providers=[provider],
    )

    with pytest.raises(ValueError, match="Duplicate tool name"):
        await target._construct_request_body_async(
            conversation=[Message.from_prompt(prompt="Read a note", role="user")],
            json_config=JsonResponseConfig(enabled=False),
        )
