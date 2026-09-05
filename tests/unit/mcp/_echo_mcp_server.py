# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tiny FastMCP stdio server used by the MCP unit tests.

Declares two tools over the real MCP stdio transport so the client wrapper is
exercised end-to-end (subprocess launch, initialize handshake, list_tools,
call_tool) without any network access.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-server")


@mcp.tool()
def echo(text: str) -> str:
    """Return the input text verbatim."""
    return f"echo: {text}"


@mcp.tool()
def add_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return str(a + b)  # type: ignore[return-value]


if __name__ == "__main__":
    mcp.run(transport="stdio")
