# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for :class:`pyrit.tools.MCPClient` and the
:class:`pyrit.tools.MCPServerSpec` union.

Coverage map (rows from the C2/C3 test matrix):

* **U10** — ``test_real_subprocess_dispatch_returns_text_content``,
  ``test_sequential_dispatch_against_real_server``.
* **U14** — ``test_connect_async_populates_schemas_via_tools_list``.
* **U17** — ``test_dispatch_timeout_returns_error_envelope``.
* **U20** — ``test_remote_mcp_server_spec_raises_not_implemented``,
  ``test_docker_mcp_server_spec_raises_not_implemented``.

These tests spawn the real ``tests/unit/tools/echo_mcp_server.py``
subprocess via ``mcp.client.stdio.stdio_client``; they exercise the
full handshake → ``tools/list`` → ``tools/call`` round trip. The
purpose is to verify that ``MCPClient`` is a thin, correct facade
over the SDK rather than to re-test the SDK itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pyrit.tools import (
    DockerMCPServerSpec,
    LocalMCPServerSpec,
    MCPClient,
    RemoteMCPServerSpec,
    ToolCall,
)

ECHO_SERVER_SCRIPT = str(Path(__file__).parent / "echo_mcp_server.py")


def _local_spec(*, timeout_seconds: float = 5.0) -> LocalMCPServerSpec:
    """Build a :class:`LocalMCPServerSpec` that spawns ``echo_mcp_server.py``."""
    return LocalMCPServerSpec(
        command=sys.executable,
        args=(ECHO_SERVER_SCRIPT,),
        timeout_seconds=timeout_seconds,
    )


def _make_call(name: str, *, call_id: str = "c1", arguments: dict | None = None) -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments=arguments or {})


@pytest.mark.asyncio
async def test_real_subprocess_dispatch_returns_text_content() -> None:
    """U10: dispatching a single tool call returns the echo server's text response."""
    client = MCPClient(spec=_local_spec())
    async with client:
        envelope = await client.dispatch_async(_make_call("echo", arguments={"text": "hi"}))
    assert envelope["is_error"] is False
    assert envelope["content"] == "hi"


@pytest.mark.asyncio
async def test_sequential_dispatch_against_real_server() -> None:
    """U10: multiple sequential calls round-trip through the same session."""
    client = MCPClient(spec=_local_spec())
    async with client:
        envelopes = [
            await client.dispatch_async(_make_call("echo", arguments={"text": "first"})),
            await client.dispatch_async(_make_call("add", arguments={"a": 2, "b": 3})),
            await client.dispatch_async(_make_call("reverse", arguments={"text": "abc"})),
        ]
    contents = [e["content"] for e in envelopes]
    assert contents == ["first", "5", "cba"]


@pytest.mark.asyncio
async def test_connect_async_populates_schemas_via_tools_list() -> None:
    """U14: schemas are discovered via tools/list during connect_async."""
    client = MCPClient(spec=_local_spec())
    async with client:
        schemas = client.schemas
    names = {s["name"] for s in schemas}
    assert names == {"echo", "add", "reverse", "slow_echo"}
    echo_schema = next(s for s in schemas if s["name"] == "echo")
    assert "parameters" in echo_schema
    assert echo_schema["parameters"]["properties"]["text"]["type"] == "string"


@pytest.mark.asyncio
async def test_dispatch_timeout_returns_error_envelope() -> None:
    """U17: a tool call that exceeds the spec's timeout produces an error envelope."""
    client = MCPClient(spec=_local_spec(timeout_seconds=0.05))
    async with client:
        envelope = await client.dispatch_async(
            _make_call("slow_echo", arguments={"text": "late", "delay_ms": 500}),
        )
    assert envelope["is_error"] is True
    assert envelope["error"] == "tool_timeout"
    assert envelope["tool"] == "slow_echo"


@pytest.mark.asyncio
async def test_dispatch_async_returns_error_envelope_on_unknown_tool() -> None:
    """Server-side errors (unknown tool name) surface as is_error envelopes."""
    client = MCPClient(spec=_local_spec())
    async with client:
        envelope = await client.dispatch_async(_make_call("nonexistent_tool"))
    assert envelope["is_error"] is True
    assert envelope["tool"] == "nonexistent_tool"


def test_remote_mcp_server_spec_is_frozen_dataclass() -> None:
    """U20: RemoteMCPServerSpec exists in the type system as a frozen dataclass."""
    spec = RemoteMCPServerSpec(url="https://example.com/mcp")
    assert spec.url == "https://example.com/mcp"
    with pytest.raises((AttributeError, Exception)):  # frozen dataclass guard
        spec.url = "other"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_remote_mcp_server_spec_raises_not_implemented() -> None:
    """U20: connecting to a RemoteMCPServerSpec raises NotImplementedError."""
    client = MCPClient(spec=RemoteMCPServerSpec(url="https://example.com/mcp"))
    with pytest.raises(NotImplementedError, match="follow-up PR"):
        await client.connect_async()


def test_docker_mcp_server_spec_dataclass_fields() -> None:
    """U20: DockerMCPServerSpec carries the fields the sandbox PR will consume."""
    spec = DockerMCPServerSpec(image="pyrit-sandbox:base")
    assert spec.image == "pyrit-sandbox:base"
    assert spec.network_profile == "none"
    assert spec.name_prefix is None
    assert spec.timeout_seconds == 30.0


@pytest.mark.asyncio
async def test_docker_mcp_server_spec_raises_not_implemented() -> None:
    """U20: connecting to a DockerMCPServerSpec raises NotImplementedError."""
    client = MCPClient(spec=DockerMCPServerSpec(image="pyrit-sandbox:base"))
    with pytest.raises(NotImplementedError, match="follow-up PR"):
        await client.connect_async()


@pytest.mark.asyncio
async def test_dispatch_before_connect_raises_runtime_error() -> None:
    """Calling dispatch_async before connect_async is a programmer error."""
    client = MCPClient(spec=_local_spec())
    with pytest.raises(RuntimeError, match="not connected"):
        await client.dispatch_async(_make_call("echo", arguments={"text": "hi"}))


@pytest.mark.asyncio
async def test_close_async_is_idempotent() -> None:
    """Calling close_async twice (or before connect) does not raise."""
    client = MCPClient(spec=_local_spec())
    await client.close_async()  # before connect — no-op.
    await client.connect_async()
    await client.close_async()
    await client.close_async()  # double-close — no-op.


@pytest.mark.asyncio
async def test_local_mcp_server_spec_is_frozen() -> None:
    """LocalMCPServerSpec is a frozen dataclass."""
    spec = LocalMCPServerSpec(command="python", args=("a.py",))
    with pytest.raises((AttributeError, Exception)):
        spec.command = "other"  # type: ignore[misc]
