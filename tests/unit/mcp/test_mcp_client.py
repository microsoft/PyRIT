# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""End-to-end tests for the MCP client wrapper over a real stdio server."""

import sys
from pathlib import Path

import pytest

from pyrit.mcp._client import MCPClientSession
from pyrit.mcp.mcp_server_config import MCPServerConfig, MCPTransport

_SERVER_PATH = Path(__file__).parent / "_echo_mcp_server.py"


@pytest.fixture
def stdio_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="test-server",
        transport=MCPTransport.STDIO,
        command=sys.executable,
        args=["-u", str(_SERVER_PATH)],
    )


async def test_list_tools_over_stdio(stdio_config):
    async with MCPClientSession(config=stdio_config) as session:
        tools = await session.list_tools()

    names = {tool.name for tool in tools}
    assert names == {"echo", "add_numbers"}
    echo_tool = next(tool for tool in tools if tool.name == "echo")
    assert "verbatim" in echo_tool.description
    assert "text" in echo_tool.input_schema.get("properties", {})


async def test_call_tool_over_stdio(stdio_config):
    async with MCPClientSession(config=stdio_config) as session:
        result = await session.call_tool(tool_name="echo", arguments={"text": "hello pyrit"})

    assert result.is_error is False
    assert result.text == "echo: hello pyrit"


async def test_call_tool_reports_tool_level_errors(stdio_config):
    """Server-side tool errors surface as is_error, not as transport exceptions."""
    async with MCPClientSession(config=stdio_config) as session:
        result = await session.call_tool(tool_name="echo", arguments={"wrong_arg": 1})

    assert result.is_error is True


async def test_call_tool_without_connection_raises():
    session = MCPClientSession(
        config=MCPServerConfig(
            name="never-connected",
            transport=MCPTransport.STDIO,
            command=sys.executable,
            args=["-c", "pass"],
        )
    )
    with pytest.raises(RuntimeError, match="not connected"):
        await session.call_tool(tool_name="echo", arguments={})
