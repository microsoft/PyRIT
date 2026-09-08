# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for thin-client authentication helpers."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import AuthenticationRecord, AuthenticationRequiredError, CredentialUnavailableError

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


def test_backend_auth_config_rejects_non_mapping_payload() -> None:
    with pytest.raises(CliAuthenticationError, match="unsupported authentication contract"):
        BackendAuthConfig.from_payload([])


def test_backend_auth_config_rejects_non_string_identifiers() -> None:
    with pytest.raises(CliAuthenticationError, match="invalid Entra tenant or client"):
        BackendAuthConfig.from_payload({"enabled": False, "tenantId": 1, "clientId": "client-id"})


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
        {
            "enabled": True,
            "tenantId": "",
            "clientId": "client-id",
            "scopes": ["https://graph.microsoft.com/User.Read"],
        },
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

    with patch.object(
        _auth,
        "_create_device_code_provider_async",
        new_callable=AsyncMock,
        return_value=provider,
    ) as create_device_code:
        result = await create_token_provider_async(
            auth_config=_AUTH_CONFIG,
            auth_mode="auto",
            interactive=True,
        )

    assert result is provider
    create_device_code.assert_awaited_once_with(auth_config=_AUTH_CONFIG)
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


async def test_create_device_code_provider_uses_persistent_cache(tmp_path: Path) -> None:
    cache_options = MagicMock()
    credential = MagicMock()
    record_path = tmp_path / "record.json"

    with (
        patch.object(_auth, "_authentication_record_path", return_value=record_path),
        patch("azure.identity.TokenCachePersistenceOptions", return_value=cache_options) as cache_type,
        patch("azure.identity.DeviceCodeCredential", return_value=credential) as credential_type,
    ):
        provider = await _auth._create_device_code_provider_async(auth_config=_AUTH_CONFIG)

    cache_type.assert_called_once_with(
        name=f"pyrit-copyrit-{_auth._authentication_cache_key(auth_config=_AUTH_CONFIG)}"
    )
    credential_type.assert_called_once_with(
        tenant_id="tenant-id",
        client_id="client-id",
        authentication_record=None,
        cache_persistence_options=cache_options,
        disable_automatic_authentication=True,
        prompt_callback=_auth._print_device_code_prompt,
    )
    assert isinstance(provider, _auth._DeviceCodeTokenProvider)


async def test_create_device_code_provider_reuses_authentication_record(tmp_path: Path) -> None:
    record_path = tmp_path / "record.json"
    authentication_record = MagicMock()

    with (
        patch.object(_auth, "_authentication_record_path", return_value=record_path),
        patch.object(_auth, "_load_authentication_record", return_value=authentication_record),
        patch("azure.identity.TokenCachePersistenceOptions"),
        patch("azure.identity.DeviceCodeCredential") as credential_type,
    ):
        provider = await _auth._create_device_code_provider_async(auth_config=_AUTH_CONFIG)

    assert credential_type.call_args.kwargs["authentication_record"] is authentication_record
    assert isinstance(provider, _auth._DeviceCodeTokenProvider)
    assert provider._has_authentication_record is True


async def test_azure_cli_provider_caches_token_until_refresh_window() -> None:
    credential = MagicMock()
    credential.get_token = AsyncMock()
    credential.close = AsyncMock()
    credential.get_token.return_value = MagicMock(token="access-token", expires_on=2_000_000_000)
    provider = _auth._AzureCliTokenProvider(
        credential=credential,
        auth_config=_AUTH_CONFIG,
    )

    with patch("pyrit.cli._auth.time.time", return_value=1_000_000_000):
        first = await provider.get_token_async()
        second = await provider.get_token_async()

    assert first == second == "access-token"
    credential.get_token.assert_awaited_once_with("https://graph.microsoft.com/.default")


async def test_azure_cli_provider_refreshes_expired_token() -> None:
    credential = MagicMock()
    credential.get_token = AsyncMock(
        side_effect=[
            MagicMock(token="first", expires_on=1_000_000_100),
            MagicMock(token="second", expires_on=2_000_000_000),
        ]
    )
    provider = _auth._AzureCliTokenProvider(credential=credential, auth_config=_AUTH_CONFIG)

    with patch("pyrit.cli._auth.time.time", return_value=1_000_000_000):
        assert await provider.get_token_async() == "first"
        assert await provider.get_token_async() == "second"

    assert credential.get_token.await_count == 2


@pytest.mark.parametrize("exception_type", [ClientAuthenticationError, CredentialUnavailableError])
async def test_azure_cli_provider_wraps_authentication_failure(exception_type: type[Exception]) -> None:
    credential = MagicMock()
    credential.get_token = AsyncMock(side_effect=exception_type("failed"))
    provider = _auth._AzureCliTokenProvider(credential=credential, auth_config=_AUTH_CONFIG)

    with pytest.raises(CliAuthenticationError, match="az login --tenant tenant-id"):
        await provider.get_token_async()


async def test_azure_cli_provider_rejects_empty_token_and_closes() -> None:
    credential = MagicMock()
    credential.get_token = AsyncMock(return_value=MagicMock(token="", expires_on=2_000_000_000))
    credential.close = AsyncMock()
    provider = _auth._AzureCliTokenProvider(credential=credential, auth_config=_AUTH_CONFIG)

    with pytest.raises(CliAuthenticationError, match="empty access token"):
        await provider.get_token_async()
    await provider.close_async()

    credential.close.assert_awaited_once()


def test_create_azure_cli_provider_uses_discovered_tenant() -> None:
    credential = MagicMock()

    with patch("azure.identity.aio.AzureCliCredential", return_value=credential) as credential_type:
        provider = _auth._create_azure_cli_provider(auth_config=_AUTH_CONFIG)

    credential_type.assert_called_once_with(tenant_id="tenant-id")
    assert isinstance(provider, _auth._AzureCliTokenProvider)


async def test_device_code_provider_authenticates_once_and_persists_record(tmp_path: Path) -> None:
    credential = MagicMock()
    authentication_record = MagicMock()
    credential.authenticate.return_value = authentication_record
    credential.get_token.return_value = MagicMock(token="access-token", expires_on=2_000_000_000)
    record_path = tmp_path / "record.json"
    provider = _auth._DeviceCodeTokenProvider(
        credential=credential,
        auth_config=_AUTH_CONFIG,
        authentication_record_path=record_path,
        has_authentication_record=False,
    )

    with (
        patch("pyrit.cli._auth.time.time", return_value=1_000_000_000),
        patch.object(_auth, "_save_authentication_record") as save_record,
    ):
        assert await provider.get_token_async() == "access-token"
        assert await provider.get_token_async() == "access-token"

    credential.authenticate.assert_called_once_with(scopes=_AUTH_CONFIG.scopes)
    save_record.assert_called_once_with(authentication_record=authentication_record, path=record_path)
    credential.get_token.assert_called_once_with(*_AUTH_CONFIG.scopes)


async def test_device_code_provider_uses_loaded_record_without_authenticating(tmp_path: Path) -> None:
    credential = MagicMock()
    credential.get_token.return_value = MagicMock(token="access-token", expires_on=2_000_000_000)
    provider = _auth._DeviceCodeTokenProvider(
        credential=credential,
        auth_config=_AUTH_CONFIG,
        authentication_record_path=tmp_path / "record.json",
        has_authentication_record=True,
    )

    assert await provider.get_token_async() == "access-token"

    credential.authenticate.assert_not_called()


async def test_device_code_provider_reauthenticates_and_updates_stale_record(tmp_path: Path) -> None:
    credential = MagicMock()
    authentication_record = MagicMock()
    credential.authenticate.return_value = authentication_record
    credential.get_token.side_effect = [
        AuthenticationRequiredError(_AUTH_CONFIG.scopes),
        MagicMock(token="access-token", expires_on=2_000_000_000),
    ]
    record_path = tmp_path / "record.json"
    provider = _auth._DeviceCodeTokenProvider(
        credential=credential,
        auth_config=_AUTH_CONFIG,
        authentication_record_path=record_path,
        has_authentication_record=True,
    )

    with patch.object(_auth, "_save_authentication_record") as save_record:
        assert await provider.get_token_async() == "access-token"

    credential.authenticate.assert_called_once_with(scopes=_AUTH_CONFIG.scopes)
    save_record.assert_called_once_with(authentication_record=authentication_record, path=record_path)
    assert credential.get_token.call_count == 2


async def test_device_code_provider_reports_cache_failure(tmp_path: Path) -> None:
    cache_error = ValueError("Cache encryption is impossible because libsecret is unavailable")
    auth_error = ClientAuthenticationError("Authentication failed")
    auth_error.__cause__ = cache_error
    credential = MagicMock()
    credential.authenticate.side_effect = auth_error
    provider = _auth._DeviceCodeTokenProvider(
        credential=credential,
        auth_config=_AUTH_CONFIG,
        authentication_record_path=tmp_path / "record.json",
        has_authentication_record=False,
    )

    with pytest.raises(CliAuthenticationError, match="Encrypted token caching is unavailable"):
        await provider.get_token_async()


async def test_device_code_provider_reports_entra_failure(tmp_path: Path) -> None:
    credential = MagicMock()
    credential.get_token.side_effect = CredentialUnavailableError("failed")
    provider = _auth._DeviceCodeTokenProvider(
        credential=credential,
        auth_config=_AUTH_CONFIG,
        authentication_record_path=tmp_path / "record.json",
        has_authentication_record=True,
    )

    with pytest.raises(CliAuthenticationError, match="device-code authentication is enabled"):
        await provider.get_token_async()


async def test_device_code_provider_rejects_empty_token_and_closes(tmp_path: Path) -> None:
    credential = MagicMock()
    credential.get_token.return_value = MagicMock(token="", expires_on=2_000_000_000)
    provider = _auth._DeviceCodeTokenProvider(
        credential=credential,
        auth_config=_AUTH_CONFIG,
        authentication_record_path=tmp_path / "record.json",
        has_authentication_record=True,
    )

    with pytest.raises(CliAuthenticationError, match="empty access token"):
        await provider.get_token_async()
    await provider.close_async()

    credential.close.assert_called_once()


def test_authentication_record_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "record.json"
    authentication_record = AuthenticationRecord(
        tenant_id="tenant-id",
        client_id="client-id",
        authority="https://login.microsoftonline.com",
        home_account_id="home-account-id",
        username="user@example.com",
    )

    _auth._save_authentication_record(authentication_record=authentication_record, path=path)
    result = _auth._load_authentication_record(auth_config=_AUTH_CONFIG, path=path)

    assert result.tenant_id == "tenant-id"
    assert result.client_id == "client-id"


def test_save_authentication_record_wraps_serialization_failure(tmp_path: Path) -> None:
    authentication_record = MagicMock()
    authentication_record.serialize.side_effect = ValueError("invalid record")

    with pytest.raises(CliAuthenticationError, match="Could not save"):
        _auth._save_authentication_record(
            authentication_record=authentication_record,
            path=tmp_path / "record.json",
        )


def test_load_authentication_record_returns_none_when_absent(tmp_path: Path) -> None:
    assert _auth._load_authentication_record(auth_config=_AUTH_CONFIG, path=tmp_path / "missing.json") is None


def test_load_authentication_record_rejects_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(CliAuthenticationError, match="Could not read"):
        _auth._load_authentication_record(auth_config=_AUTH_CONFIG, path=path)


def test_load_authentication_record_rejects_wrong_client(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    authentication_record = AuthenticationRecord(
        tenant_id="tenant-id",
        client_id="other-client",
        authority="https://login.microsoftonline.com",
        home_account_id="home-account-id",
        username="user@example.com",
    )
    path.write_text(authentication_record.serialize(), encoding="utf-8")

    with pytest.raises(CliAuthenticationError, match="does not match"):
        _auth._load_authentication_record(auth_config=_AUTH_CONFIG, path=path)


def test_load_authentication_record_accepts_tenant_alias(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    authentication_record = AuthenticationRecord(
        tenant_id="tenant-guid",
        client_id="client-id",
        authority="https://login.microsoftonline.com",
        home_account_id="home-account-id",
        username="user@example.com",
    )
    path.write_text(authentication_record.serialize(), encoding="utf-8")
    auth_config = BackendAuthConfig(
        enabled=True,
        tenant_id="contoso.onmicrosoft.com",
        client_id="client-id",
        scopes=_AUTH_CONFIG.scopes,
    )

    result = _auth._load_authentication_record(auth_config=auth_config, path=path)

    assert result.tenant_id == "tenant-guid"


def test_authentication_record_path_uses_hashed_identity() -> None:
    path = _auth._authentication_record_path(auth_config=_AUTH_CONFIG)

    assert path.parent.name == ".pyrit_cache"
    assert "tenant-id" not in path.name
    assert "client-id" not in path.name


def test_device_code_prompt_uses_stderr(capsys) -> None:
    expires_on = datetime(2026, 1, 1, tzinfo=timezone.utc)

    _auth._print_device_code_prompt("https://microsoft.com/devicelogin", "ABCD-EFGH", expires_on)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ABCD-EFGH" in captured.err
    assert expires_on.isoformat() in captured.err


def test_is_interactive_requires_stdin_and_stderr_ttys() -> None:
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stderr.isatty", return_value=False),
    ):
        assert _auth._is_interactive() is False


async def test_verify_provider_closes_after_authentication_failure() -> None:
    provider = MagicMock()
    provider.get_token_async = AsyncMock(side_effect=CliAuthenticationError("failed"))
    provider.close_async = AsyncMock()

    with pytest.raises(CliAuthenticationError, match="failed"):
        await _auth._verify_provider_async(provider=provider)

    provider.close_async.assert_awaited_once()
