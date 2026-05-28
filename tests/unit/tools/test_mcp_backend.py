# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for :class:`pyrit.tools.MCPToolBackend`.

These tests verify the multi-server fan-out and routing layer on top of
:class:`MCPClient`: schema aggregation, name-collision detection,
``name_prefix`` disambiguation, ``allowed_tools`` allow-list semantics,
and concurrent-dispatch serialization. They reuse the real
``echo_mcp_server.py`` stdio subprocess.

Coverage map:

* **U18** — ``test_disallowed_tool_returns_error_envelope_without_invoking_server``.
* **U20a** — ``test_name_collision_raises_value_error``.
* **U20b** — ``test_name_prefix_disambiguates_colliding_servers``.
* **U21** — ``test_concurrent_dispatch_is_serialized_by_lock``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from pyrit.tools import (
    LocalMCPServerSpec,
    MCPToolBackend,
    ToolCall,
)

ECHO_SERVER_SCRIPT = str(Path(__file__).parent / "echo_mcp_server.py")


def _spec(*, name_prefix: str | None = None, timeout_seconds: float = 5.0) -> LocalMCPServerSpec:
    return LocalMCPServerSpec(
        command=sys.executable,
        args=(ECHO_SERVER_SCRIPT,),
        name_prefix=name_prefix,
        timeout_seconds=timeout_seconds,
    )


def _make_call(name: str, *, call_id: str = "c1", arguments: dict | None = None) -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments=arguments or {})


@pytest.mark.asyncio
async def test_backend_aggregates_schemas_across_servers() -> None:
    """Schemas from every connected server show up in :attr:`schemas`."""
    backend = MCPToolBackend(servers=[_spec()])
    async with backend:
        names = {s["name"] for s in backend.schemas}
    assert names == {"echo", "add", "reverse", "slow_echo"}


@pytest.mark.asyncio
async def test_dispatch_routes_to_correct_server() -> None:
    """A :class:`ToolCall` is routed to the server that registered the name."""
    backend = MCPToolBackend(servers=[_spec()])
    async with backend:
        envelope = await backend.dispatch_async(_make_call("echo", arguments={"text": "routed"}))
    assert envelope["is_error"] is False
    assert envelope["content"] == "routed"


@pytest.mark.asyncio
async def test_name_collision_raises_value_error() -> None:
    """Two servers exposing the same tool name without prefixes raise."""
    backend = MCPToolBackend(servers=[_spec(), _spec()])
    with pytest.raises(ValueError, match="duplicate tool name"):
        await backend.__aenter__()
    # __aexit__ is the cleanup path; __aenter__ failing leaves nothing to clean.


@pytest.mark.asyncio
async def test_name_prefix_disambiguates_colliding_servers() -> None:
    """Setting :attr:`LocalMCPServerSpec.name_prefix` disambiguates duplicates."""
    backend = MCPToolBackend(
        servers=[
            _spec(name_prefix="a_"),
            _spec(name_prefix="b_"),
        ],
    )
    async with backend:
        names = {s["name"] for s in backend.schemas}
        assert "a_echo" in names
        assert "b_echo" in names
        envelope = await backend.dispatch_async(_make_call("a_echo", arguments={"text": "alpha"}))
        assert envelope["content"] == "alpha"
        envelope_b = await backend.dispatch_async(_make_call("b_echo", arguments={"text": "beta"}))
        assert envelope_b["content"] == "beta"


@pytest.mark.asyncio
async def test_disallowed_tool_returns_error_envelope_without_invoking_server() -> None:
    """U18: allowed_tools blocks both schema advertisement AND dispatch."""
    backend = MCPToolBackend(servers=[_spec()], allowed_tools=["echo"])
    async with backend:
        advertised = {s["name"] for s in backend.schemas}
        assert advertised == {"echo"}  # add/reverse/slow_echo are filtered out.

        envelope = await backend.dispatch_async(_make_call("add", arguments={"a": 1, "b": 2}))
    assert envelope["is_error"] is True
    assert envelope["error"] == "tool_not_allowed"
    assert envelope["tool"] == "add"
    assert envelope["allowed_tools"] == ["echo"]


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_envelope() -> None:
    """A call to a name no connected server exposes returns an error envelope."""
    backend = MCPToolBackend(servers=[_spec()])
    async with backend:
        envelope = await backend.dispatch_async(_make_call("never_registered"))
    assert envelope["is_error"] is True
    assert envelope["error"] == "tool_not_registered"
    assert envelope["tool"] == "never_registered"


@pytest.mark.asyncio
async def test_concurrent_dispatch_is_serialized_by_lock() -> None:
    """U21: two coroutines dispatching against the same backend do not interleave.

    The slow_echo tool sleeps server-side; without the lock the two
    dispatches would issue overlapping JSON-RPC frames over the same
    stdio pipe. With the lock they run back-to-back. We assert both
    return successfully — interleaved frames would surface as protocol
    errors or wrong content.
    """
    backend = MCPToolBackend(servers=[_spec(timeout_seconds=10.0)])
    async with backend:
        results = await asyncio.gather(
            backend.dispatch_async(_make_call("slow_echo", arguments={"text": "A", "delay_ms": 50})),
            backend.dispatch_async(_make_call("slow_echo", arguments={"text": "B", "delay_ms": 50})),
        )
    assert all(not r["is_error"] for r in results)
    assert {r["content"] for r in results} == {"A", "B"}


@pytest.mark.asyncio
async def test_dispatch_all_sequential_async_preserves_order() -> None:
    """Bulk dispatch returns (call, envelope) pairs in declaration order."""
    backend = MCPToolBackend(servers=[_spec()])
    calls = [
        _make_call("echo", call_id="c1", arguments={"text": "first"}),
        _make_call("echo", call_id="c2", arguments={"text": "second"}),
        _make_call("echo", call_id="c3", arguments={"text": "third"}),
    ]
    async with backend:
        results = await backend.dispatch_all_sequential_async(calls)
    assert [c.call_id for c, _ in results] == ["c1", "c2", "c3"]
    assert [r["content"] for _, r in results] == ["first", "second", "third"]
