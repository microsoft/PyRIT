# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import inspect
import io
import json
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TextIO
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import ValidationError

from pyrit.exceptions import RateLimitException
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
    assert provider.identifier["type"] == "MCPToolProvider"
    assert provider.identifier["server_name"] == "notes"
    assert provider.identifier["transport"] == "http"
    assert len(str(provider.identifier["configuration_fingerprint"])) == 64


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
    assert provider.identifier["type"] == "MCPToolProvider"
    assert provider.identifier["server_name"] == "notes"
    assert provider.identifier["transport"] == "stdio"
    assert len(str(provider.identifier["configuration_fingerprint"])) == 64


def test_identifier_fingerprints_server_selection_without_credentials() -> None:
    first = MCPToolProvider(
        server_name="notes",
        server_config=MCPStreamableHTTPServerConfig(
            url="https://one.example/mcp",
            headers={"Authorization": "first-secret"},
        ),
    )
    same_server = MCPToolProvider(
        server_name="notes",
        server_config=MCPStreamableHTTPServerConfig(
            url="https://one.example/mcp",
            headers={"Authorization": "second-secret"},
        ),
    )
    different_server = MCPToolProvider(
        server_name="notes",
        server_config=MCPStreamableHTTPServerConfig(url="https://two.example/mcp"),
    )

    first_fingerprint = first.identifier["configuration_fingerprint"]
    assert first_fingerprint == same_server.identifier["configuration_fingerprint"]
    assert first_fingerprint != different_server.identifier["configuration_fingerprint"]
    assert "one.example" not in str(first.identifier)
    assert "secret" not in str(first.identifier)


def test_stdio_identifier_fingerprints_server_selection_without_environment() -> None:
    first = MCPToolProvider(
        server_name="notes",
        server_config=MCPStdioServerConfig(
            command="python",
            args=["notes_server.py"],
            env={"TOKEN": "first-secret"},
            cwd="tools",
        ),
    )
    same_server = MCPToolProvider(
        server_name="notes",
        server_config=MCPStdioServerConfig(
            command="python",
            args=["notes_server.py"],
            env={"TOKEN": "second-secret"},
            cwd="tools",
        ),
    )
    different_server = MCPToolProvider(
        server_name="notes",
        server_config=MCPStdioServerConfig(
            command="python",
            args=["other_server.py"],
            cwd="tools",
        ),
    )

    first_fingerprint = first.identifier["configuration_fingerprint"]
    assert first_fingerprint == same_server.identifier["configuration_fingerprint"]
    assert first_fingerprint != different_server.identifier["configuration_fingerprint"]
    assert "notes_server.py" not in str(first.identifier)
    assert "secret" not in str(first.identifier)


def test_from_config_accepts_validated_config() -> None:
    config = MCPConfig(
        servers={
            "notes": MCPStreamableHTTPServerConfig(url="http://127.0.0.1:8000/mcp/notes"),
        }
    )

    provider = MCPToolProvider.from_config(config=config, server_name="notes")

    assert provider.server_config is config.servers["notes"]


@pytest.mark.parametrize("stream_state", ["normal", "captured", "missing", "closed"])
async def test_create_session_async_uses_stdio_transport(stream_state: str) -> None:
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
    original_stderr = MagicMock(spec=io.TextIOWrapper)
    original_stderr.fileno.return_value = 2
    current_stderr: TextIO | None
    if stream_state == "normal":
        current_stderr = MagicMock(spec=io.TextIOWrapper)
        current_stderr.fileno.return_value = 5
    elif stream_state == "missing":
        current_stderr = None
    elif stream_state == "closed":
        current_stderr = io.TextIOWrapper(io.BytesIO())
        current_stderr.close()
    else:
        current_stderr = io.StringIO()
    expected_stderr = current_stderr if stream_state == "normal" else original_stderr

    @asynccontextmanager
    async def fake_stdio_client_async(
        parameters: StdioServerParameters, *, errlog: TextIO
    ) -> AsyncGenerator[tuple[MagicMock, MagicMock], None]:
        nonlocal stdio_parameters
        stdio_parameters = parameters
        assert errlog is expected_stderr
        assert errlog.fileno() == (5 if stream_state == "normal" else 2)
        yield streams

    session = MagicMock(spec=ClientSession)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.initialize = AsyncMock()
    client_session = MagicMock(return_value=session)

    with (
        patch.object(sys, "stderr", current_stderr),
        patch.object(sys, "__stderr__", original_stderr),
        patch("pyrit.prompt_target.common.mcp_tool_provider.stdio_client", fake_stdio_client_async),
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


@pytest.mark.parametrize("stream_state", ["missing", "captured", "closed"])
async def test_create_session_async_rejects_unusable_stderr(stream_state: str) -> None:
    provider = MCPToolProvider(server_name="notes", server_config=MCPStdioServerConfig(command="python"))
    stream = None if stream_state == "missing" else io.TextIOWrapper(io.BytesIO())
    if stream_state == "closed":
        assert stream is not None
        stream.close()

    with (
        patch.object(sys, "stderr", stream),
        patch.object(sys, "__stderr__", stream),
        patch("pyrit.prompt_target.common.mcp_tool_provider.stdio_client") as client,
    ):
        with pytest.raises(RuntimeError, match="MCP stdio requires a stderr stream with a file descriptor"):
            async with provider._create_session_async():
                pytest.fail("An MCP session must not start without usable stderr")
    client.assert_not_called()


async def test_create_session_async_uses_mcp_http_timeouts() -> None:
    provider = MCPToolProvider(
        server_name="notes",
        server_config=MCPStreamableHTTPServerConfig(
            url="http://127.0.0.1:8000/mcp/notes",
            headers={"Authorization": "Bearer token"},
        ),
    )
    streams = (MagicMock(), MagicMock())
    http_client_used: httpx2.AsyncClient | None = None

    @asynccontextmanager
    async def fake_streamable_http_client_async(
        url: str, *, http_client: httpx2.AsyncClient
    ) -> AsyncGenerator[tuple[MagicMock, MagicMock], None]:
        nonlocal http_client_used
        assert url == "http://127.0.0.1:8000/mcp/notes"
        assert isinstance(http_client, httpx2.AsyncClient)
        assert isinstance(provider.server_config, MCPStreamableHTTPServerConfig)
        assert http_client.headers["Authorization"] == provider.server_config.headers["Authorization"]
        assert http_client.timeout == httpx2.Timeout(30.0, read=300.0)
        assert not http_client.is_closed
        http_client_used = http_client
        yield streams

    session = MagicMock(spec=ClientSession)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.initialize = AsyncMock()
    client_session = MagicMock(return_value=session)

    with (
        patch(
            "pyrit.prompt_target.common.mcp_tool_provider.streamable_http_client",
            fake_streamable_http_client_async,
        ),
        patch("pyrit.prompt_target.common.mcp_tool_provider.ClientSession", client_session),
    ):
        async with provider._create_session_async() as created_session:
            assert created_session is session

    assert http_client_used is not None
    assert http_client_used.is_closed
    client_session.assert_called_once_with(*streams)
    session.initialize.assert_awaited_once()


async def test_get_tools_async_discovers_all_pages() -> None:
    provider = _http_provider()
    session = MagicMock(spec=ClientSession)
    session.list_tools = AsyncMock(
        side_effect=[
            ListToolsResult(
                tools=[
                    Tool(
                        name="get_note",
                        description="Read a note",
                        input_schema={
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                        },
                    )
                ],
                next_cursor="page-2",
            ),
            ListToolsResult(
                tools=[
                    Tool(
                        name="list_notes",
                        description="List notes",
                        input_schema={"type": "object", "properties": {}},
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
    assert session.list_tools.call_args_list[0].kwargs["params"].cursor is None
    assert session.list_tools.call_args_list[1].kwargs["params"].cursor == "page-2"


async def test_call_tool_async_returns_mcp_result() -> None:
    provider = _http_provider()
    provider._tools = [
        Tool(
            name="get_note",
            description="Read a note",
            input_schema={"type": "object", "properties": {}},
        )
    ]
    session = MagicMock()
    session.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text='{"text":"Welcome"}')],
            structured_content={"text": "Welcome"},
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


@pytest.mark.parametrize("is_error", [False, True])
@pytest.mark.parametrize("structured_content", [None, {}, {"text": "Welcome"}])
def test_serialize_call_result_preserves_wire_fields(
    *, is_error: bool, structured_content: dict[str, object] | None
) -> None:
    result = CallToolResult.model_validate(
        {
            "content": [{"type": "text", "text": "Welcome"}],
            "isError": is_error,
            "structuredContent": structured_content,
        }
    )
    expected: dict[str, object] = {
        "content": [{"type": "text", "text": "Welcome"}],
        "is_error": is_error,
    }
    if structured_content is not None:
        expected["structured_content"] = structured_content

    assert MCPToolProvider._serialize_call_result(result=result) == expected


async def test_provider_reuses_session_within_execution_scope() -> None:
    provider = _http_provider()
    session = MagicMock()
    session.list_tools = AsyncMock(
        return_value=ListToolsResult(
            tools=[
                Tool(
                    name="get_note",
                    description="Read a note",
                    input_schema={"type": "object", "properties": {}},
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
    target._suppress_tools = False
    target._run_tool_call_loop_async = AsyncMock(return_value=[])  # type: ignore[method-assign]
    send_implementation = inspect.unwrap(OpenAIResponseTarget._send_prompt_to_target_async)

    result = await send_implementation(target, normalized_conversation=[])

    assert result == []
    assert lifecycle == ["enter", "exit"]
    target._run_tool_call_loop_async.assert_awaited_once_with(normalized_conversation=[])


async def test_openai_response_target_retries_request_inside_provider_scope() -> None:
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
    target._suppress_tools = False
    target._execute_tools = True
    target._get_json_response_config = MagicMock(return_value=JsonResponseConfig(enabled=False))  # type: ignore[method-assign]
    target._construct_request_body_async = AsyncMock(return_value={})  # type: ignore[method-assign]
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="Done",
            )
        ]
    )
    target._handle_openai_request_async = AsyncMock(  # type: ignore[method-assign]
        side_effect=[RateLimitException(message="transient rate limit"), response]
    )
    target._find_pending_tool_calls = MagicMock(return_value=[])  # type: ignore[method-assign]
    request = Message.from_prompt(prompt="Read a note", role="user")
    send_implementation = inspect.unwrap(OpenAIResponseTarget._send_prompt_to_target_async)

    result = await send_implementation(target, normalized_conversation=[request])

    assert result == [response]
    assert target._handle_openai_request_async.await_count == 2
    assert lifecycle == ["enter", "exit"]


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
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
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
    assert output.output == {"structured_content": {"text": "Welcome"}}
    assert output.invoked is True


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
