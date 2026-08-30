# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the public authentication configuration route."""

from unittest.mock import patch

from pyrit.backend.routes.auth import get_auth_config_async


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
