# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the public authentication configuration routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.middleware.auth import AuthenticatedUser, EntraAuthMiddleware
from pyrit.backend.routes.auth import get_auth_access_async, get_auth_config_async, well_known_router

METADATA_PATH = "/.well-known/oauth-protected-resource/api/mcp"
ENTRA_POLICY = AuthenticationPolicy(
    mode="entra", tenant_id="tenant-id", client_id="client-id", allowed_group_ids=("group-1",)
)


def _request_with_policy(policy: AuthenticationPolicy) -> Request:
    app = FastAPI()
    app.state.authentication_policy = policy
    return Request({"type": "http", "app": app})


def _metadata_app(*, policy: AuthenticationPolicy, cors_origins: frozenset[str] = frozenset()) -> FastAPI:
    app = FastAPI()
    app.state.authentication_policy = policy
    app.state.cors_origins = cors_origins
    app.add_middleware(EntraAuthMiddleware)
    app.include_router(well_known_router)
    return app


async def test_get_auth_config_returns_enabled_graph_contract() -> None:
    policy = AuthenticationPolicy(
        mode="entra",
        tenant_id="tenant-id",
        client_id="client-id",
        allowed_group_ids=("group-1", "group-2"),
    )
    result = await get_auth_config_async(_request_with_policy(policy))

    assert result == {
        "enabled": True,
        "clientId": "client-id",
        "tenantId": "tenant-id",
        "allowedGroupIds": "group-1,group-2",
        "scopes": ["https://graph.microsoft.com/User.Read"],
    }


async def test_get_auth_config_returns_disabled_contract_when_configuration_is_absent() -> None:
    result = await get_auth_config_async(_request_with_policy(AuthenticationPolicy(mode="local")))

    assert result == {
        "enabled": False,
        "clientId": "",
        "tenantId": "",
        "allowedGroupIds": "",
        "scopes": [],
    }


async def test_get_auth_access_returns_authenticated_admin_state() -> None:
    request = MagicMock(spec=Request)
    request.state.user = AuthenticatedUser(
        oid="user-1",
        name="Admin",
        email="admin@example.com",
        groups=["admin-group"],
        is_admin=True,
    )

    assert await get_auth_access_async(request) == {"isAdmin": True}


async def test_get_auth_access_uses_explicit_local_admin_override() -> None:
    request = _request_with_policy(AuthenticationPolicy(mode="local", allow_unauthenticated_admin=True))
    request.state.user = None

    assert await get_auth_access_async(request) == {"isAdmin": True}


@pytest.mark.parametrize(
    ("base_url", "resource"),
    [
        ("http://localhost:8000", "http://localhost:8000/api/mcp"),
        ("http://127.0.0.1:8781", "http://127.0.0.1:8781/api/mcp"),
    ],
)
def test_mcp_resource_metadata_is_public_and_names_the_url_the_client_used(base_url: str, resource: str) -> None:
    with patch.object(EntraAuthMiddleware, "_authenticate_request_async", new_callable=AsyncMock) as authenticate:
        response = TestClient(_metadata_app(policy=ENTRA_POLICY), base_url=base_url).get(METADATA_PATH)

    authenticate.assert_not_awaited()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "resource": resource,
        "authorization_servers": ["https://login.microsoftonline.com/tenant-id/v2.0"],
        "scopes_supported": ["https://graph.microsoft.com/User.Read"],
        "bearer_methods_supported": ["header"],
        "resource_name": "PyRIT",
    }


# Container Apps forwards HTTPS as HTTP; Front Door also replaces the host with the Container Apps one.
@pytest.mark.parametrize(
    ("headers", "cors_origins", "resource"),
    [
        ({"X-Forwarded-Proto": "https"}, {"https://aca.example"}, "https://aca.example/api/mcp"),
        (
            {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "fd.example"},
            {"https://aca.example", "https://fd.example"},
            "https://fd.example/api/mcp",
        ),
        (
            {"X-Forwarded-Proto": "https, http", "X-Forwarded-Host": "fd.example, aca.example"},
            {"https://fd.example"},
            "https://fd.example/api/mcp",
        ),
        (
            {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example"},
            {"https://fd.example"},
            "http://aca.example/api/mcp",
        ),
        ({"X-Forwarded-Proto": "https", "X-Forwarded-Host": "fd.example"}, {"*"}, "http://aca.example/api/mcp"),
    ],
)
def test_mcp_resource_metadata_uses_a_forwarded_origin_only_when_it_is_listed(
    headers: dict[str, str], cors_origins: set[str], resource: str
) -> None:
    app = _metadata_app(policy=ENTRA_POLICY, cors_origins=frozenset(cors_origins))

    response = TestClient(app, base_url="http://aca.example").get(METADATA_PATH, headers=headers)

    assert response.json()["resource"] == resource


def test_mcp_resource_metadata_is_not_found_when_entra_is_off() -> None:
    response = TestClient(_metadata_app(policy=AuthenticationPolicy(mode="local"))).get(METADATA_PATH)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert response.headers["cache-control"] == "no-store"
