# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for backend configuration file storage."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services.configuration_file_service import (
    ConfigurationFileConflictError,
    ConfigurationFileService,
    _download_blob_config_async,
    _is_azure_blob_uri,
    _upload_blob_config_async,
)


async def test_configuration_file_service_reads_and_updates_local_file(tmp_path: Path) -> None:
    """Test reading and updating a local configuration file."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("operator: before\n", encoding="utf-8")
    service = ConfigurationFileService(config_file_value=str(config_path))

    assert await service.read_async() == "operator: before\n"
    _, version = await service.read_with_version_async()

    await service.update_async("operator: after\n", expected_version=version)

    assert config_path.read_text(encoding="utf-8") == "operator: after\n"
    assert service.source == str(config_path)


@pytest.mark.parametrize(
    "blob_uri",
    [
        "https://attacker.blob.example.com/config/config.yaml",
        "https://blob.core.windows.net/config/config.yaml",
        "https://account.blob.core.windows.net/config",
        "http://account.blob.core.windows.net/config/config.yaml",
    ],
)
def test_configuration_file_service_rejects_untrusted_blob_uri(blob_uri: str) -> None:
    assert _is_azure_blob_uri(blob_uri) is False


def test_configuration_file_service_accepts_known_azure_blob_host() -> None:
    assert _is_azure_blob_uri("https://account.blob.core.windows.net/config/config.yaml") is True


async def test_configuration_file_service_rejects_invalid_content_before_write(tmp_path: Path) -> None:
    """Test that invalid YAML does not replace the existing configuration."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("operator: before\n", encoding="utf-8")
    service = ConfigurationFileService(config_file_value=str(config_path))
    _, version = await service.read_with_version_async()

    with pytest.raises(ValueError, match="Invalid YAML configuration"):
        await service.update_async("operator: [unterminated\n", expected_version=version)

    assert config_path.read_text(encoding="utf-8") == "operator: before\n"


async def test_configuration_file_service_rejects_semantically_invalid_content_before_write(tmp_path: Path) -> None:
    """Test that loader validation failures do not replace the existing configuration."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("operator: before\n", encoding="utf-8")
    service = ConfigurationFileService(config_file_value=str(config_path))
    _, version = await service.read_with_version_async()

    with pytest.raises(ValueError, match="env_akv_strict must be a bool"):
        await service.update_async("env_akv_strict: invalid\n", expected_version=version)

    assert config_path.read_text(encoding="utf-8") == "operator: before\n"


async def test_configuration_file_service_rejects_quoted_custom_initializer_flag(tmp_path: Path) -> None:
    """Test that a string cannot enable custom initializers through truthiness."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("allow_custom_initializers: false\n", encoding="utf-8")
    service = ConfigurationFileService(config_file_value=str(config_path))
    _, version = await service.read_with_version_async()

    with pytest.raises(ValueError, match="allow_custom_initializers must be a bool"):
        await service.update_async('allow_custom_initializers: "false"\n', expected_version=version)

    assert config_path.read_text(encoding="utf-8") == "allow_custom_initializers: false\n"


async def test_blob_helpers_use_sas_authentication() -> None:
    """Test downloading and uploading directly with a SAS-authenticated blob URI."""
    blob_uri = "https://account.blob.core.windows.net/config/config.yaml?sig=secret"
    blob_stream = MagicMock()
    blob_stream.readall = AsyncMock(return_value=b"operator: alice\n")
    blob_client = MagicMock()
    blob_client.__aenter__ = AsyncMock(return_value=blob_client)
    blob_client.__aexit__ = AsyncMock(return_value=None)
    blob_client.download_blob = AsyncMock(return_value=blob_stream)
    blob_client.upload_blob = AsyncMock()

    with patch("azure.storage.blob.aio.BlobClient.from_blob_url", return_value=blob_client) as client_factory:
        assert await _download_blob_config_async(blob_uri) == b"operator: alice\n"
        await _upload_blob_config_async(blob_uri=blob_uri, content=b"operator: bob\n")

    assert client_factory.call_count == 2
    assert client_factory.call_args_list[0].kwargs == {"blob_url": blob_uri}
    blob_client.upload_blob.assert_awaited_once_with(b"operator: bob\n", overwrite=True)


async def test_blob_helpers_use_default_credential_without_sas() -> None:
    """Test downloading and uploading with the default Azure credential."""
    blob_uri = "https://account.blob.core.windows.net/config/config.yaml"
    credential = MagicMock()
    credential.__aenter__ = AsyncMock(return_value=credential)
    credential.__aexit__ = AsyncMock(return_value=None)
    blob_stream = MagicMock()
    blob_stream.readall = AsyncMock(return_value=b"operator: alice\n")
    blob_client = MagicMock()
    blob_client.__aenter__ = AsyncMock(return_value=blob_client)
    blob_client.__aexit__ = AsyncMock(return_value=None)
    blob_client.download_blob = AsyncMock(return_value=blob_stream)
    blob_client.upload_blob = AsyncMock()

    with (
        patch("azure.identity.aio.DefaultAzureCredential", return_value=credential) as credential_factory,
        patch("azure.storage.blob.aio.BlobClient.from_blob_url", return_value=blob_client) as client_factory,
    ):
        assert await _download_blob_config_async(blob_uri) == b"operator: alice\n"
        await _upload_blob_config_async(blob_uri=blob_uri, content=b"operator: bob\n")

    assert credential_factory.call_count == 2
    assert client_factory.call_args_list[0].kwargs == {"blob_url": blob_uri, "credential": credential}
    blob_client.upload_blob.assert_awaited_once_with(b"operator: bob\n", overwrite=True)


async def test_configuration_file_service_reads_and_updates_blob() -> None:
    """Test that blob-backed configuration delegates to blob storage helpers."""
    blob_uri = "https://account.blob.core.windows.net/config/config.yaml"
    service = ConfigurationFileService(config_file_value=blob_uri)
    with (
        patch(
            "pyrit.backend.services.configuration_file_service._download_blob_config_async",
            new=AsyncMock(
                side_effect=[
                    b"operator: before\n",
                    b"operator: before\n",
                    b"operator: before\n",
                    b"operator: after\n",
                ]
            ),
        ) as download_mock,
        patch(
            "pyrit.backend.services.configuration_file_service._upload_blob_config_async",
            new=AsyncMock(),
        ) as upload_mock,
    ):
        assert await service.read_async() == "operator: before\n"
        _, version = await service.read_with_version_async()
        await service.update_async("operator: after\n", expected_version=version)
        assert await service.read_async() == "operator: after\n"

    assert download_mock.await_count == 4
    assert download_mock.call_args_list[0].args == (blob_uri,)
    assert download_mock.call_args_list[3].args == (blob_uri,)
    upload_mock.assert_awaited_once_with(blob_uri=blob_uri, content=b"operator: after\n")


async def test_configuration_file_service_creates_missing_local_source(tmp_path: Path) -> None:
    """Test bootstrapping a configuration file from a missing-source version."""
    config_path = tmp_path / "nested" / "config.yaml"
    service = ConfigurationFileService(config_file_value=str(config_path))

    content, version = await service.read_with_version_async()
    updated_version = await service.update_async("operator: created\n", expected_version=version)

    assert content == ""
    assert updated_version != version
    assert config_path.read_text(encoding="utf-8") == "operator: created\n"


async def test_configuration_file_service_rejects_stale_version(tmp_path: Path) -> None:
    """Test that an external edit cannot be overwritten with a stale version."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("operator: before\n", encoding="utf-8")
    service = ConfigurationFileService(config_file_value=str(config_path))
    _, version = await service.read_with_version_async()
    config_path.write_text("operator: external\n", encoding="utf-8")

    with pytest.raises(ConfigurationFileConflictError, match="reload"):
        await service.update_async("operator: stale\n", expected_version=version)

    assert config_path.read_text(encoding="utf-8") == "operator: external\n"


async def test_configuration_file_service_reads_latest_blob_content() -> None:
    """Test that every read observes the current blob content."""
    blob_uri = "https://account.blob.core.windows.net/config/config.yaml"
    service = ConfigurationFileService(config_file_value=blob_uri)
    with patch(
        "pyrit.backend.services.configuration_file_service._download_blob_config_async",
        new=AsyncMock(side_effect=[b"operator: before\n", b"operator: external\n"]),
    ) as download_mock:
        assert await service.read_async() == "operator: before\n"
        assert await service.read_async() == "operator: external\n"

    assert download_mock.await_count == 2


def test_configuration_file_service_source_omits_blob_credentials() -> None:
    """Test that the display source does not expose SAS query parameters."""
    service = ConfigurationFileService(
        config_file_value="https://account.blob.core.windows.net/config/config.yaml?sp=rw&sig=secret"
    )

    assert service.source == "https://account.blob.core.windows.net/config/config.yaml"


async def test_configuration_file_service_resolves_no_explicit_source_as_none() -> None:
    """Test that an unset environment value preserves optional loader defaults."""
    service = ConfigurationFileService(config_file_value=None)

    async with service.resolve_async() as config_path:
        assert config_path is None
