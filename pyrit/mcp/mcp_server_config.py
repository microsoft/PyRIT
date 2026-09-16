# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Configuration models for connecting PyRIT to MCP servers.

These models describe *how to reach* an MCP server (transport and connection
parameters) plus the red-team policy applied to its tools (allowlist, caps).
They are transport-agnostic inputs to :mod:`pyrit.mcp._client`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MCPTransport(str, Enum):
    """
    Transports supported by :class:`MCPServerConfig`.

    - ``stdio``: launch the server as a subprocess and speak MCP over stdin/stdout.
      Deterministic and offline-testable; the recommended transport for local tools.
    - ``streamable_http``: connect to a remote MCP server over the Streamable HTTP
      transport (the successor of the deprecated HTTP+SSE transport in the MCP spec).
    """

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerConfig(BaseModel):
    """
    Connection and policy configuration for a single MCP server.

    Exactly one connection style must be provided per transport: ``command`` for
    stdio servers, ``url`` for streamable-HTTP servers.

    Args:
        name (str): Friendly server name used in tool catalog entries, audit logs,
            and error messages.
        transport (MCPTransport): Transport used to reach the server.
        command (str | None): Executable that starts the MCP server (stdio only),
            e.g. ``"python"``.
        args (list[str] | None): Arguments forwarded to ``command`` (stdio only).
        env (dict[str, str] | None): Extra environment variables for the server
            subprocess (stdio only). Defaults to a minimal environment when unset.
        url (str | None): Server endpoint URL (streamable-http only).
        headers (dict[str, str] | None): Extra HTTP headers for the endpoint
            (streamable-http only), e.g. authorization headers.
        allowed_tools (list[str] | None): Explicit tool-name allowlist for this
            server. ``None`` (default) allows every tool the server declares;
            names listed here are the only ones the wrapped target may execute.
        tool_call_timeout (float): Per-tool-call timeout in seconds. Defaults to 60.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Friendly server name used in logs and the tool catalog.")
    transport: MCPTransport = Field(description="Transport used to reach the MCP server.")
    command: str | None = Field(None, description="Executable that starts the MCP server (stdio only).")
    args: list[str] = Field(default_factory=list, description="Arguments for the stdio server command.")
    env: dict[str, str] | None = Field(None, description="Extra environment variables for the stdio subprocess.")
    url: str | None = Field(None, description="Server endpoint URL (streamable-http only).")
    headers: dict[str, str] | None = Field(None, description="Extra HTTP headers (streamable-http only).")
    allowed_tools: list[str] | None = Field(
        None,
        description="Explicit tool-name allowlist for this server; None allows all tools the server declares.",
    )
    tool_call_timeout: float = Field(
        60.0,
        gt=0,
        description="Per-tool-call timeout in seconds.",
    )

    def validate_connection(self) -> None:
        """
        Verify the connection parameters match the selected transport.

        Raises:
            ValueError: If required connection parameters are missing or a
                parameter is provided that does not belong to the transport.
        """
        if self.transport is MCPTransport.STDIO:
            if not self.command:
                raise ValueError(f"MCP server '{self.name}': stdio transport requires 'command'.")
            if self.url:
                raise ValueError(f"MCP server '{self.name}': 'url' is only valid for the streamable_http transport.")
        elif self.transport is MCPTransport.STREAMABLE_HTTP:
            if not self.url:
                raise ValueError(f"MCP server '{self.name}': streamable_http transport requires 'url'.")
            if self.command:
                raise ValueError(f"MCP server '{self.name}': 'command' is only valid for the stdio transport.")
