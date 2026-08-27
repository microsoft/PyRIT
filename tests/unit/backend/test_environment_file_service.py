# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for backend environment file storage."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.backend.services.environment_file_service import EnvironmentFileService, _update_akv_document_async


async def test_environment_file_service_uses_default_candidates(tmp_path: Path) -> None:
    """Test that None selects PyRIT's default .env and .env.local candidates."""
    with patch("pyrit.backend.services.environment_file_service.CONFIGURATION_DIRECTORY_PATH", tmp_path):
        service = EnvironmentFileService(resolved_env_files=None)

    items = await service.list_async()

    assert [item.name for item in items] == [".env", ".env.local"]
    assert [item.exists for item in items] == [False, False]


async def test_environment_file_service_preserves_explicit_order_and_updates(tmp_path: Path) -> None:
    """Test explicit file ordering, lazy content reads, and updates."""
    first = tmp_path / "first.env"
    second = tmp_path / ".env.local"
    first.write_text("FIRST=before\n", encoding="utf-8")
    second.write_text("SECOND=value\n", encoding="utf-8")
    service = EnvironmentFileService(resolved_env_files=[first, second])

    items = await service.list_async()
    loaded = await service.read_async(file_id="0")
    updated = await service.update_async(file_id="0", content="FIRST=after\n")

    assert [item.path for item in items] == [str(first), str(second)]
    assert items[0].content == ""
    assert loaded.content == "FIRST=before\n"
    assert updated.content == "FIRST=after\n"
    assert first.read_text(encoding="utf-8") == "FIRST=after\n"


async def test_environment_file_service_empty_list_has_no_files() -> None:
    """Test that an explicit empty list disables all environment files."""
    service = EnvironmentFileService(resolved_env_files=[])

    assert await service.list_async() == []


async def test_environment_file_service_lists_and_updates_akv_before_files(tmp_path: Path) -> None:
    """Test that an AKV bootstrap document is editable and precedes local files."""
    env_file = tmp_path / ".env.local"
    env_file.write_text("LOCAL=value\n", encoding="utf-8")
    secret_url = "https://vault.vault.azure.net/secrets/bootstrap"
    service = EnvironmentFileService(resolved_env_files=[env_file], env_akv_ref=[secret_url])

    with (
        patch(
            "pyrit.backend.services.environment_file_service._fetch_akv_document_async",
            new=AsyncMock(
                side_effect=[
                    ("AKV=before\n", "https://vault.vault.azure.net"),
                    ("AKV=after\n", "https://vault.vault.azure.net"),
                ]
            ),
        ) as fetch_mock,
        patch(
            "pyrit.backend.services.environment_file_service._update_akv_document_async",
            new=AsyncMock(return_value="AKV=after\n"),
        ) as update_mock,
    ):
        items = await service.list_async()
        fetch_mock.assert_not_awaited()
        loaded = await service.read_async(file_id="akv:0")
        updated = await service.update_async(file_id="akv:0", content="AKV=after\n")
        loaded_after_update = await service.read_async(file_id="akv:0")

    assert [item.id for item in items] == ["akv:0", "0"]
    assert items[0].name == "AKV: bootstrap"
    assert items[0].path == secret_url
    assert items[0].content == ""
    assert loaded.content == "AKV=before\n"
    assert loaded_after_update.content == "AKV=after\n"
    assert fetch_mock.await_count == 2
    assert updated.content == "AKV=after\n"
    update_mock.assert_awaited_once_with(secret_url=secret_url, content="AKV=after\n", strict=True)


async def test_environment_file_service_rejects_versioned_akv_update() -> None:
    """Test that editing cannot silently bypass a pinned AKV secret version."""
    with patch("azure.identity.aio.DefaultAzureCredential") as credential_mock:
        with pytest.raises(ValueError, match="read-only"):
            await _update_akv_document_async(
                secret_url="https://vault.vault.azure.net/secrets/bootstrap/version-1",
                content="AKV=after\n",
                strict=True,
            )

    credential_mock.assert_not_called()


async def test_environment_file_service_reads_latest_akv_content() -> None:
    """Test that every read observes the current AKV content."""
    secret_url = "https://vault.vault.azure.net/secrets/bootstrap"
    service = EnvironmentFileService(resolved_env_files=[], env_akv_ref=[secret_url])
    with patch(
        "pyrit.backend.services.environment_file_service._fetch_akv_document_async",
        new=AsyncMock(side_effect=[("FIRST=1\n", "vault"), ("SECOND=2\n", "vault")]),
    ) as fetch_mock:
        assert (await service.read_async(file_id="akv:0")).content == "FIRST=1\n"
        assert (await service.read_async(file_id="akv:0")).content == "SECOND=2\n"

    assert fetch_mock.await_count == 2
