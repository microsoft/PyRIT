# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Serve the backend's REST API to external MCP clients as tools."""

from collections.abc import AsyncGenerator, Collection
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, ClassVar

from mcp import types
from mcp.server.lowlevel.server import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from pyrit.backend.rest_tools import RestToolset

if TYPE_CHECKING:
    from mcp.server.context import ServerRequestContext
    from starlette.applications import Starlette


class MCPEndpoint:
    """
    An ASGI app that serves REST operations as tools over MCP Streamable HTTP at ``PATH``.

    The path is under ``/api``, so the backend's authentication and runtime admission apply to it,
    and each tool call is a REST request made with the MCP caller's credentials. Each backend
    lifespan gets a fresh transport, because an MCP session manager runs only once.

    Responses are JSON, so the endpoint accepts only POST: a GET would open an event stream that
    stays connected, and the backend counts open requests as active work. A request with an Origin
    header must come from an allowed origin, so a web page cannot reach the tools through a
    browser, for example by pointing its own host name at this server.
    """

    PATH: ClassVar[str] = "/api/mcp"
    # Browser CORS must allow the headers MCP clients send: the protocol version after initialization, the
    # method on every request, and the name on a tool call.
    CORS_HEADERS: ClassVar[tuple[str, ...]] = ("MCP-Protocol-Version", "Mcp-Method", "Mcp-Name")

    def __init__(self, *, toolset: RestToolset, allowed_origins: Collection[str]) -> None:
        """
        Serve the toolset's operations.

        Args:
            toolset (RestToolset): The REST operations to offer.
            allowed_origins (Collection[str]): Browser origins allowed to call the endpoint, as configured
                for CORS, where "*" allows any. Requests without an Origin header, such as those from MCP
                clients outside a browser, are allowed.
        """
        self._toolset = toolset
        self._allowed_origins = None if "*" in allowed_origins else frozenset(allowed_origins)
        self._server: Server[Any] = Server(
            "PyRIT",
            instructions=RestToolset.GUIDE,
            on_list_tools=self._list_tools_async,
            on_call_tool=self._call_tool_async,
        )
        self._app: Starlette | None = None

    @asynccontextmanager
    async def run_async(self) -> AsyncGenerator[None, None]:
        """Serve requests until the context exits."""
        app = self._server.streamable_http_app(
            streamable_http_path=self.PATH,
            stateless_http=True,
            json_response=True,
            # The endpoint checks Origin itself. The SDK's host check only allows localhost, which
            # would reject every deployed host name.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        try:
            async with self._server.session_manager.run():
                self._app = app
                try:
                    yield
                finally:
                    self._app = None
        except BaseExceptionGroup as group:
            # The session manager's task group wraps an error raised by the application during its
            # lifespan; let that error through unchanged.
            if len(group.exceptions) != 1:
                raise
            raise group.exceptions[0] from None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass a POST from an allowed origin to the running transport, or answer with an error."""
        origin = Headers(scope=scope).get("origin")
        if scope["method"] != "POST":
            response = JSONResponse(
                {"detail": "The MCP endpoint accepts only POST."}, status_code=405, headers={"Allow": "POST"}
            )
        elif origin is not None and self._allowed_origins is not None and origin not in self._allowed_origins:
            response = JSONResponse({"detail": f"Origin '{origin}' may not call the MCP endpoint."}, status_code=403)
        elif self._app is None:
            response = JSONResponse({"detail": "The MCP endpoint is not running."}, status_code=503)
        else:
            await self._app(scope, receive, send)
            return
        await response(scope, receive, send)

    async def _list_tools_async(  # pyrit-keyword-only-exempt MCP Server on_list_tools handler (ctx, params)
        self, context: "ServerRequestContext[Any, Any]", params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(name=operation.name, description=operation.description, input_schema=operation.input_schema)
                for operation in self._toolset.operations
            ]
        )

    async def _call_tool_async(  # pyrit-keyword-only-exempt MCP Server on_call_tool handler (ctx, params)
        self, context: "ServerRequestContext[Any, Any]", params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        request = context.request
        text, failed = await self._toolset.call_async(
            name=params.name,
            arguments=params.arguments or {},
            authorization=request.headers.get("authorization") if isinstance(request, Request) else None,
        )
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=failed)
