# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A standalone MCP example with in-memory notes."""

import argparse

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel


class NoteText(BaseModel):
    text: str


def create_server() -> MCPServer[None]:
    server: MCPServer[None] = MCPServer("example-notes")
    notes = {"welcome": "Welcome to the example notebook."}

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
    def get_note(id: str) -> NoteText:
        """Read a note by ID. Unknown IDs return an error."""
        if id not in notes:
            raise ValueError(f"Unknown note: {id}")
        return NoteText(text=notes[id])

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standalone example notes MCP server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="streamable-http",
    )
    args = parser.parse_args()
    server = create_server()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=args.port,
            streamable_http_path="/mcp/notes",
        )
