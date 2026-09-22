# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the public authentication configuration route."""

from unittest.mock import MagicMock, patch

from starlette.requests import Request

from pyrit.backend.middleware.auth import AuthenticatedUser
from pyrit.backend.routes.auth import get_auth_access_async, get_auth_config_async


async def test_get_auth_config_returns_enabled_graph_contract() -> None:
    environment = {
        "ENTRA_TENANT_ID": " tenant-id ",
        "ENTRA_CLIENT_ID": " client-id ",
        "ENTRA_ALLOWED_GROUP_IDS": " group-1,group-2 ",
    }

    with patch.dict("os.environ", environment, clear=False):
        result = await get_auth_config_async()

    assert result == {
        "enabled": True,
        "clientId": "client-id",
        "tenantId": "tenant-id",
        "allowedGroupIds": "group-1,group-2",
        "scopes": ["https://graph.microsoft.com/User.Read"],
    }


async def test_get_auth_config_returns_disabled_contract_when_configuration_is_absent() -> None:
    environment = {
        "ENTRA_TENANT_ID": "",
        "ENTRA_CLIENT_ID": "",
        "ENTRA_ALLOWED_GROUP_IDS": "",
    }

    with patch.dict("os.environ", environment, clear=False):
        result = await get_auth_config_async()

    assert result == {
        "enabled": False,
        "clientId": "",
        "tenantId": "",
        "allowedGroupIds": "",
        "scopes": [],
    }


async def test_get_auth_config_does_not_enable_incomplete_configuration() -> None:
    environment = {
        "ENTRA_TENANT_ID": "tenant-id",
        "ENTRA_CLIENT_ID": "",
        "ENTRA_ALLOWED_GROUP_IDS": "group-1",
    }

    with patch.dict("os.environ", environment, clear=False):
        result = await get_auth_config_async()

    assert result["enabled"] is False
    assert result["scopes"] == []


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
    request = MagicMock(spec=Request)
    request.state.user = None

    with patch.dict("os.environ", {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "true"}, clear=False):
        assert await get_auth_access_async(request) == {"isAdmin": True}
