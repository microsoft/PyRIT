# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the public authentication configuration route."""

from unittest.mock import MagicMock

from fastapi import FastAPI
from starlette.requests import Request

from pyrit.backend.authentication_policy import AuthenticationPolicy
from pyrit.backend.middleware.auth import AuthenticatedUser
from pyrit.backend.routes.auth import get_auth_access_async, get_auth_config_async


def _request_with_policy(policy: AuthenticationPolicy) -> Request:
    app = FastAPI()
    app.state.authentication_policy = policy
    return Request({"type": "http", "app": app})


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
