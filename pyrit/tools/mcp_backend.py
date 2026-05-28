# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Multi-server tool backend that proxies dispatch through one or more
MCP servers.

This is the :class:`~pyrit.tools.ToolBackend` implementation that real
red-team configurations use. It composes one
:class:`~pyrit.tools.MCPClient` per :class:`~pyrit.tools.MCPServerSpec`,
aggregates their advertised schemas, routes incoming
:class:`~pyrit.tools.ToolCall` instances to the correct underlying
client, and enforces an optional ``allowed_tools`` allow-list.

Contrast with :class:`~pyrit.tools.LocalToolBackend`, which dispatches
to Python ``async def`` callables inside PyRIT's own process.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from pyrit.tools.backend import ToolBackend
from pyrit.tools.mcp_client import MCPClient

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pyrit.tools.mcp_client import MCPServerSpec
    from pyrit.tools.models import ToolCall

logger = logging.getLogger(__name__)


class MCPToolBackend(ToolBackend):
    """
    :class:`~pyrit.tools.ToolBackend` backed by one or more MCP servers.

    On :meth:`__aenter__`, the backend spawns / connects each server in
    its :attr:`_servers` list (sequentially) through a single
    :class:`contextlib.AsyncExitStack`, runs the MCP handshake, caches
    schemas, and builds an advertised-name → ``(client, server_name)``
    routing table. Collisions raise :class:`ValueError` unless the
    colliding specs set :attr:`~pyrit.tools.LocalMCPServerSpec.name_prefix`.

    A single shared :class:`AsyncExitStack` (rather than one per client)
    is required so anyio's nested cancel scopes — opened by the ``mcp``
    SDK's ``stdio_client`` and ``ClientSession`` context managers — are
    closed in strict LIFO order from the entering task. Closing
    out-of-order would trip
    ``"Attempted to exit a cancel scope that isn't the current task's
    current cancel scope"``.

    Dispatch is serialized through an :class:`asyncio.Lock` per backend
    instance — multiple concurrent coroutines sharing the same backend
    (e.g. parallel attack runs) will not interleave JSON-RPC frames on
    the same stdio pipe.
    """

    def __init__(
        self,
        *,
        servers: Iterable[MCPServerSpec],
        allowed_tools: list[str] | None = None,
    ) -> None:
        """
        Initialize the backend.

        Args:
            servers: One or more :class:`MCPServerSpec` instances describing
                where each server runs.
            allowed_tools: Optional allow-list of tool names. Names not in
                the list are filtered from :attr:`schemas` AND
                short-circuit dispatch with a ``tool_not_allowed`` envelope.
                Names are matched after :attr:`~LocalMCPServerSpec.name_prefix`
                has been applied. Defaults to None (every advertised tool is
                callable).

        Raises:
            ValueError: When *servers* is empty.
        """
        self._servers: list[MCPServerSpec] = list(servers)
        if not self._servers:
            raise ValueError("MCPToolBackend requires at least one server spec.")
        self._allowed_tools: set[str] | None = set(allowed_tools) if allowed_tools is not None else None
        self._clients: list[MCPClient] = []
        self._routing: dict[str, tuple[MCPClient, str]] = {}
        self._dispatch_lock = asyncio.Lock()
        self._stack: AsyncExitStack | None = None
        self._entered = False

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """The union of every connected server's schemas, filtered by ``allowed_tools``."""
        out: list[dict[str, Any]] = []
        for client in self._clients:
            for schema in client.schemas:
                if self._allowed_tools is not None and schema["name"] not in self._allowed_tools:
                    continue
                out.append(schema)
        return out

    async def __aenter__(self) -> MCPToolBackend:
        """
        Connect each underlying client through a shared :class:`AsyncExitStack` and build the routing table.

        Returns:
            MCPToolBackend: *self*, ready to dispatch.

        Raises:
            ValueError: When two connected clients advertise the same tool
                name without a disambiguating ``name_prefix``.
        """
        stack = AsyncExitStack()
        clients: list[MCPClient] = []
        routing: dict[str, tuple[MCPClient, str]] = {}
        try:
            for spec in self._servers:
                client = MCPClient(spec=spec)
                await stack.enter_async_context(client)
                clients.append(client)
                for advertised_name in client.tool_names:
                    if advertised_name in routing:
                        raise ValueError(
                            f"duplicate tool name '{advertised_name}'. "
                            "Set LocalMCPServerSpec.name_prefix on at least one "
                            "colliding server to disambiguate.",
                        )
                    routing[advertised_name] = (client, advertised_name)
        except Exception:
            await stack.aclose()
            raise

        self._stack = stack
        self._clients = clients
        self._routing = routing
        self._entered = True
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Tear down every underlying client in strict LIFO order."""
        stack = self._stack
        self._stack = None
        self._clients = []
        self._routing = {}
        self._entered = False
        if stack is not None:
            await stack.aclose()

    async def dispatch_async(self, call: ToolCall) -> dict[str, Any]:
        """
        Route *call* to the correct client and dispatch.

        See :class:`MCPClient.dispatch_async` for the envelope shape.
        Allow-list rejections and unknown-tool calls return error
        envelopes; only "backend not entered" raises.

        Args:
            call (ToolCall): The call to dispatch.

        Returns:
            dict[str, Any]: A structured envelope (success, ``tool_not_allowed``,
                ``tool_not_registered``, or the underlying
                :meth:`MCPClient.dispatch_async` envelope).

        Raises:
            RuntimeError: When the backend has not been entered via ``async with``.
        """
        if not self._entered:
            raise RuntimeError(
                "MCPToolBackend is not active. Use `async with backend:` to manage its lifecycle before dispatching.",
            )

        if self._allowed_tools is not None and call.name not in self._allowed_tools:
            logger.info("Rejecting disallowed tool call: %s", call.name)
            return {
                "is_error": True,
                "error": "tool_not_allowed",
                "tool": call.name,
                "allowed_tools": sorted(self._allowed_tools),
            }

        route = self._routing.get(call.name)
        if route is None:
            available = sorted(self._routing.keys())
            logger.warning("Tool '%s' not registered. Available: %s", call.name, available)
            return {
                "is_error": True,
                "error": "tool_not_registered",
                "tool": call.name,
                "available_tools": available,
            }

        client, _server_side_name = route
        async with self._dispatch_lock:
            return await client.dispatch_async(call)
