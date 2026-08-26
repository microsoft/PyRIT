# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for thin-client authentication helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.cli import _auth
from pyrit.cli._auth import BackendAuthConfig, CliAuthenticationError, create_token_provider_async

_AUTH_CONFIG = BackendAuthConfig(
    enabled=True,
    tenant_id="tenant-id",
    client_id="client-id",
    scopes=("https://graph.microsoft.com/User.Read",),
)


def test_backend_auth_config_parses_enabled_contract() -> None:
    result = BackendAuthConfig.from_payload(
        {
            "enabled": True,
            "tenantId": " tenant-id ",
            "clientId": " client-id ",
            "scopes": [" https://graph.microsoft.com/User.Read "],
        }
    )

    assert result == _AUTH_CONFIG


def test_backend_auth_config_accepts_legacy_disabled_contract() -> None:
    result = BackendAuthConfig.from_payload(
        {
            "tenantId": "",
            "clientId": "",
            "allowedGroupIds": "",
        }
    )

    assert result == BackendAuthConfig(enabled=False, tenant_id="", client_id="", scopes=())


def test_backend_auth_config_rejects_non_graph_scope() -> None:
    with pytest.raises(CliAuthenticationError, match="unsupported authentication scope"):
        BackendAuthConfig.from_payload(
            {
                "enabled": True,
                "tenantId": "tenant-id",
                "clientId": "client-id",
                "scopes": ["https://management.azure.com/.default"],
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"enabled": "true"},
        {"enabled": True, "tenantId": "", "clientId": "client-id", "scopes": ["scope"]},
        {"enabled": True, "tenantId": "tenant-id", "clientId": "client-id", "scopes": []},
        {"enabled": True, "tenantId": "tenant-id", "clientId": "client-id", "scopes": "scope"},
    ],
)
def test_backend_auth_config_rejects_invalid_contract(payload: object) -> None:
    with pytest.raises(CliAuthenticationError):
        BackendAuthConfig.from_payload(payload)


async def test_create_token_provider_returns_none_when_server_auth_is_disabled() -> None:
    auth_config = BackendAuthConfig(enabled=False, tenant_id="", client_id="", scopes=())

    result = await create_token_provider_async(auth_config=auth_config, auth_mode="auto")

    assert result is None


async def test_create_token_provider_auto_uses_device_code() -> None:
    provider = MagicMock()
    provider.get_token_async = AsyncMock(return_value="token")
    provider.close_async = AsyncMock()

    with patch.object(_auth, "_create_device_code_provider", return_value=provider) as create_device_code:
        result = await create_token_provider_async(
            auth_config=_AUTH_CONFIG,
            auth_mode="auto",
            interactive=True,
        )

    assert result is provider
    create_device_code.assert_called_once_with(auth_config=_AUTH_CONFIG)
    provider.get_token_async.assert_awaited_once()
    provider.close_async.assert_not_awaited()


async def test_create_token_provider_auto_fails_fast_when_non_interactive() -> None:
    with pytest.raises(CliAuthenticationError, match="interactive terminal"):
        await create_token_provider_async(
            auth_config=_AUTH_CONFIG,
            auth_mode="auto",
            interactive=False,
        )


async def test_create_token_provider_device_code_requires_interactive_terminal() -> None:
    with pytest.raises(CliAuthenticationError, match="interactive terminal"):
        await create_token_provider_async(
            auth_config=_AUTH_CONFIG,
            auth_mode="device_code",
            interactive=False,
        )


async def test_create_token_provider_azure_cli_warns(capsys) -> None:
    provider = MagicMock()
    provider.get_token_async = AsyncMock(return_value="token")
    provider.close_async = AsyncMock()

    with patch.object(_auth, "_create_azure_cli_provider", return_value=provider):
        result = await create_token_provider_async(
            auth_config=_AUTH_CONFIG,
            auth_mode="azure_cli",
            interactive=False,
        )

    assert result is provider
    assert "permissions beyond User.Read" in capsys.readouterr().err


def test_create_device_code_provider_uses_persistent_cache() -> None:
    cache_options = MagicMock()
    credential = MagicMock()

    with (
        patch("azure.identity.TokenCachePersistenceOptions", return_value=cache_options) as cache_type,
        patch("azure.identity.DeviceCodeCredential", return_value=credential) as credential_type,
    ):
        provider = _auth._create_device_code_provider(auth_config=_AUTH_CONFIG)

    cache_type.assert_called_once_with(name="pyrit-copyrit-client-id")
    credential_type.assert_called_once_with(
        tenant_id="tenant-id",
        client_id="client-id",
        cache_persistence_options=cache_options,
    )
    assert isinstance(provider, _auth._DeviceCodeTokenProvider)


async def test_azure_cli_provider_caches_token_until_refresh_window() -> None:
    credential = MagicMock()
    credential.get_token = AsyncMock()
    credential.close = AsyncMock()
    credential.get_token.return_value = MagicMock(token="access-token", expires_on=2_000_000_000)
    provider = _auth._AzureIdentityTokenProvider(
        credential=credential,
        auth_config=_AUTH_CONFIG,
        mode="azure_cli",
    )

    with patch("pyrit.cli._auth.time.time", return_value=1_000_000_000):
        first = await provider.get_token_async()
        second = await provider.get_token_async()

    assert first == second == "access-token"
    credential.get_token.assert_awaited_once_with("https://graph.microsoft.com/.default")
