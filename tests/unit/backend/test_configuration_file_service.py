# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for backend configuration file storage."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from pyrit.backend.services.configuration_file_service import ConfigurationFileService


async def test_configuration_file_service_reads_and_updates_local_file(tmp_path: Path) -> None:
    """Test reading and updating a local configuration file."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("operator: before\n", encoding="utf-8")
    service = ConfigurationFileService(config_file_value=str(config_path))

    assert await service.read_async() == "operator: before\n"

    await service.update_async("operator: after\n")

    assert config_path.read_text(encoding="utf-8") == "operator: after\n"


async def test_configuration_file_service_reads_and_updates_blob() -> None:
    """Test that blob-backed configuration delegates to blob storage helpers."""
    blob_uri = "https://account.blob.core.windows.net/config/config.yaml"
    service = ConfigurationFileService(config_file_value=blob_uri)
    with (
        patch(
            "pyrit.backend.services.configuration_file_service._download_blob_config_async",
            new=AsyncMock(return_value=b"operator: before\n"),
        ) as download_mock,
        patch(
            "pyrit.backend.services.configuration_file_service._upload_blob_config_async",
            new=AsyncMock(),
        ) as upload_mock,
    ):
        assert await service.read_async() == "operator: before\n"
        await service.update_async("operator: after\n")
        assert await service.read_async() == "operator: after\n"

    download_mock.assert_awaited_once_with(blob_uri)
    upload_mock.assert_awaited_once_with(blob_uri=blob_uri, content=b"operator: after\n")


async def test_configuration_file_service_refreshes_blob_after_cache_expires() -> None:
    """Test that expiry returns stale content while refreshing for the next read."""
    blob_uri = "https://account.blob.core.windows.net/config/config.yaml"
    service = ConfigurationFileService(config_file_value=blob_uri)
    with (
        patch(
            "pyrit.backend.services.configuration_file_service._download_blob_config_async",
            new=AsyncMock(side_effect=[b"operator: before\n", b"operator: external\n"]),
        ) as download_mock,
        patch("pyrit.backend.services.configuration_file_service.time.monotonic", return_value=100.0) as monotonic_mock,
    ):
        assert await service.read_async() == "operator: before\n"
        assert await service.read_async() == "operator: before\n"

        monotonic_mock.return_value = 111.0
        assert await service.read_async() == "operator: before\n"
        assert service._refresh_task is not None
        await service._refresh_task
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
