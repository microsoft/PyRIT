# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A standalone FastMCP example with in-memory notes."""

import argparse
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel


class NoteText(BaseModel):
    text: str


def create_server(*, port: int = 8000) -> FastMCP[None]:
    server: FastMCP[None] = FastMCP(
        "example-notes",
        host="127.0.0.1",
        port=port,
        streamable_http_path="/mcp/notes",
    )
    notes = {"welcome": "Welcome to the example notebook."}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
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
    transport: Literal["stdio", "streamable-http"] = args.transport
    create_server(port=args.port).run(transport=transport)
