# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx2
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.routing import Route

from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.main import app as backend_app
from pyrit.backend.mcp_server import MCPEndpoint
from pyrit.backend.rest_tools import RestToolset
from unit.async_utils import TEST_WATCHDOG_SECONDS

BASE_URL = "http://testserver"
ALLOWED_ORIGIN = "http://localhost:3000"
INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
def toolset() -> Iterator[RestToolset]:
    app = FastAPI()
    app.state.compatibility_id = "0.0.0+g" + "a" * 40

    @app.get("/api/whoami")
    async def whoami_async(request: Request) -> dict[str, str | None]:
        """Report the caller's credentials."""
        return {"authorization": request.headers.get("authorization")}

    with patch.object(RestToolset, "OPERATIONS", ("whoami",)):
        yield RestToolset(app=app)


@pytest.fixture
def endpoint(toolset: RestToolset) -> MCPEndpoint:
    return MCPEndpoint(toolset=toolset, allowed_origins=[ALLOWED_ORIGIN])


def _app(endpoint: MCPEndpoint) -> Starlette:
    return Starlette(routes=[Route(MCPEndpoint.PATH, endpoint=endpoint)])


@asynccontextmanager
async def _session_async(
    endpoint: MCPEndpoint, *, headers: dict[str, str] | None = None
) -> AsyncGenerator[ClientSession, None]:
    async with (
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=_app(endpoint)), base_url=BASE_URL, headers=headers
        ) as http_client,
        streamable_http_client(f"{BASE_URL}{MCPEndpoint.PATH}", http_client=http_client) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


async def test_mcp_tools_are_rest_operations_called_with_the_callers_credentials(endpoint: MCPEndpoint) -> None:
    async with endpoint.run_async(), _session_async(endpoint, headers={"Authorization": "Bearer t"}) as session:
        tools = await session.list_tools()
        result = await session.call_tool(name="whoami", arguments={})

    assert [tool.name for tool in tools.tools] == ["whoami"]
    assert result.is_error is False
    (content,) = result.content
    assert isinstance(content, types.TextContent)
    assert json.loads(content.text) == {"authorization": "Bearer t"}


async def test_mcp_endpoint_serves_only_while_an_app_lifespan_runs(endpoint: MCPEndpoint) -> None:
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=_app(endpoint)), base_url=BASE_URL) as client:
        assert (await client.post(MCPEndpoint.PATH, json={})).status_code == 503
        for _ in range(2):
            async with endpoint.run_async(), _session_async(endpoint) as session:
                assert [tool.name for tool in (await session.list_tools()).tools] == ["whoami"]
        assert (await client.post(MCPEndpoint.PATH, json={})).status_code == 503


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT"])
async def test_mcp_endpoint_accepts_only_post(endpoint: MCPEndpoint, method: str) -> None:
    # Before this check, a GET opened an event stream that never ended, so the watchdog turns a hang into a failure.
    async with (
        asyncio.timeout(TEST_WATCHDOG_SECONDS),
        endpoint.run_async(),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=_app(endpoint)), base_url=BASE_URL) as client,
    ):
        response = await client.request(method, MCPEndpoint.PATH, headers=MCP_HEADERS)

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


# The endpoint follows the CORS origins, where "*" allows any.
@pytest.mark.parametrize(("allowed_origins", "other_origin_status"), [([ALLOWED_ORIGIN], 403), (["*"], 200)])
async def test_mcp_endpoint_checks_browser_origins_like_cors(
    toolset: RestToolset, allowed_origins: list[str], other_origin_status: int
) -> None:
    endpoint = MCPEndpoint(toolset=toolset, allowed_origins=allowed_origins)
    async with (
        endpoint.run_async(),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=_app(endpoint)), base_url=BASE_URL) as client,
    ):
        allowed = await client.post(
            MCPEndpoint.PATH, json=INITIALIZE_REQUEST, headers={**MCP_HEADERS, "Origin": ALLOWED_ORIGIN}
        )
        other = await client.post(
            MCPEndpoint.PATH, json=INITIALIZE_REQUEST, headers={**MCP_HEADERS, "Origin": "http://attacker.example"}
        )

    assert allowed.status_code == 200
    assert other.status_code == other_origin_status


def test_backend_cors_lets_browser_mcp_clients_send_their_headers() -> None:
    response = TestClient(backend_app).options(
        MCPEndpoint.PATH,
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,mcp-protocol-version,mcp-method,mcp-name",
        },
    )

    # A preflight that asks for a header CORS doesn't allow fails with 400.
    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert all(header in allowed for header in ("mcp-protocol-version", "mcp-method", "mcp-name"))


def test_backend_mcp_401_leads_to_sign_in_metadata_outside_the_api_schema() -> None:
    # The backend's own routes and middleware with an Entra policy, without starting its lifespan.
    app = FastAPI(routes=list(backend_app.router.routes), middleware=backend_app.user_middleware)
    app.state.authentication_policy = AuthenticationPolicy(
        mode="entra", tenant_id="tenant-id", client_id="client-id", allowed_group_ids=("group-1",)
    )
    app.state.cors_origins = backend_app.state.cors_origins
    client = TestClient(app)

    challenge = client.post(MCPEndpoint.PATH, json=INITIALIZE_REQUEST, headers=MCP_HEADERS)
    metadata_url = challenge.headers["www-authenticate"].split('"')[1]
    metadata = client.get(metadata_url)

    assert challenge.status_code == 401
    assert metadata_url == "http://testserver/.well-known/oauth-protected-resource/api/mcp"
    assert metadata.status_code == 200
    assert metadata.json()["resource"] == "http://testserver/api/mcp"
    assert not any(path.startswith("/.well-known") for path in backend_app.openapi()["paths"])
