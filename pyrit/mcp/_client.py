# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Thin async client over the official ``mcp`` Python SDK.

Owns the transport plumbing (stdio subprocess / Streamable HTTP sessions) and
exposes just the two operations the wrapped target needs: listing tools and
calling one. The ``mcp`` package is an optional dependency (``pyrit[mcp]``) and
is imported lazily with an actionable error message.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyrit.mcp.mcp_server_config import MCPServerConfig, MCPTransport

if TYPE_CHECKING:
    from types import TracebackType

logger = logging.getLogger(__name__)


def _import_mcp() -> Any:
    """
    Import the optional ``mcp`` package.

    Returns:
        The imported module.

    Raises:
        ModuleNotFoundError: With an actionable install hint if the extra is missing.
    """
    try:
        import mcp  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "The 'mcp' package is required for MCP support. Install it with: pip install pyrit[mcp]"
        ) from exc
    return mcp


@dataclass(frozen=True)
class MCPTool:
    """A tool declared by an MCP server (catalog entry)."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class MCPToolResult:
    """Result of an MCP tool call, normalized to text."""

    text: str
    is_error: bool


class MCPClientSession:
    """
    A live session with a single MCP server.

    Use as an async context manager; entering connects (launching the subprocess
    for stdio servers) and exiting tears the connection down. Not safe for
    concurrent use by multiple tasks — the wrapped target serializes access.
    """

    def __init__(self, *, config: MCPServerConfig) -> None:
        """
        Create (but do not start) a session for the given server.

        Args:
            config (MCPServerConfig): Connection and policy configuration.
        """
        self._config = config
        self._exit_stack: AsyncExitStack | None = None
        self._session: Any = None  # mcp.ClientSession, untyped until mcp is imported

    @property
    def server_name(self) -> str:
        """The friendly server name from the config."""
        return self._config.name

    async def __aenter__(self) -> MCPClientSession:
        mcp = _import_mcp()
        config = self._config
        self._exit_stack = AsyncExitStack()

        try:
            if config.transport is MCPTransport.STDIO:
                stdio_client = mcp.client.stdio.stdio_client
                server_params = mcp.client.stdio.StdioServerParameters(
                    command=config.command,
                    args=list(config.args),
                    env=dict(config.env) if config.env is not None else None,
                )
                read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(server_params))
            else:
                streamablehttp_client = mcp.client.streamable_http.streamablehttp_client
                http_transport = await self._exit_stack.enter_async_context(
                    streamablehttp_client(url=config.url, headers=dict(config.headers) if config.headers else {})
                )
                read_stream, write_stream, _get_session_id = http_transport

            client_session = mcp.client.session.ClientSession(read_stream, write_stream)
            self._session = await self._exit_stack.enter_async_context(client_session)
            await self._session.initialize()
        except BaseException:
            await self._exit_stack.aclose()
            self._exit_stack = None
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
        self._session = None

    def _require_session(self) -> Any:
        if self._session is None:
            raise RuntimeError(
                f"MCP session for server '{self._config.name}' is not connected; use 'async with' to connect."
            )
        return self._session

    async def list_tools(self) -> list[MCPTool]:
        """
        List the tools the server declares.

        Returns:
            list[MCPTool]: The server's tool catalog.

        Raises:
            RuntimeError: If the session is not connected.
            Exception: Propagates SDK/transport failures after normalizing
                ``result.isError``-style errors (listing has no error channel,
                so transport failures propagate).
        """
        session = self._require_session()
        response = await session.list_tools()
        return [
            MCPTool(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.inputSchema) if tool.inputSchema else {},
            )
            for tool in response.tools
        ]

    async def call_tool(self, *, tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        """
        Call a tool on the server.

        Args:
            tool_name (str): The tool's registered name.
            arguments (dict[str, Any]): JSON tool arguments.

        Returns:
            MCPToolResult: Normalized text result; ``is_error`` is True when the
                server reported a tool-level error.

        Raises:
            RuntimeError: If the session is not connected.
            Exception: Transport failures (timeouts, connection loss) propagate;
                tool-level errors do not (they are reported via ``is_error``).
        """
        session = self._require_session()
        response = await session.call_tool(name=tool_name, arguments=arguments)

        parts: list[str] = []
        for content in response.content or []:
            text = getattr(content, "text", None)
            if text is not None:
                parts.append(text)
        text = "\n".join(parts) if parts else "(no content returned)"
        return MCPToolResult(text=text, is_error=bool(response.isError))
