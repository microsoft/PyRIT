# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deterministic echo MCP server used as a stdio subprocess fixture by
``tests/unit/tools/test_mcp_client.py`` (C3) and the integration tests
(C9).

Lands in C2 so subsequent commits don't shuffle test plumbing; C2's own
tests do not import this module (the :class:`CallableToolRegistry` is
exercised in-process).

Run directly as ``python echo_mcp_server.py`` to expose the four tools
over stdio. The MCP client harness in C3 launches this file with
``mcp.client.stdio.stdio_client`` and asserts behavior end to end.
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pyrit-echo")


@mcp.tool()
def echo(text: str) -> str:
    """Return *text* unchanged."""
    return text


@mcp.tool()
def add(a: int, b: int) -> int:
    """Return ``a + b``."""
    return a + b


@mcp.tool()
def reverse(text: str) -> str:
    """Return *text* reversed."""
    return text[::-1]


@mcp.tool()
async def slow_echo(text: str, delay_ms: int = 0) -> str:
    """
    Return *text* after sleeping ``delay_ms`` milliseconds. Used by
    timeout / cancellation tests.
    """
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)
    return text


if __name__ == "__main__":
    mcp.run()
