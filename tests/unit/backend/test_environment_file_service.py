# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for backend environment file storage."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services.environment_file_service import (
    EnvironmentFileConflictError,
    EnvironmentFileService,
    _update_akv_document_async,
)


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
    first_id = items[0].id
    loaded = await service.read_async(file_id=first_id)
    updated = await service.update_async(
        file_id=first_id,
        content="FIRST=after\n",
        expected_version=loaded.version or "",
    )

    assert [item.path for item in items] == [str(first), str(second)]
    assert items[0].content == ""
    assert loaded.content == "FIRST=before\n"
    assert updated.content == "FIRST=after\n"
    assert first.read_text(encoding="utf-8") == "FIRST=after\n"

    reordered = EnvironmentFileService(resolved_env_files=[second, first])
    reordered_items = await reordered.list_async()
    assert next(item.id for item in reordered_items if item.path == str(first)) == first_id


async def test_environment_file_service_preserves_local_content_without_validation(tmp_path: Path) -> None:
    """Test local saves preserve content handled permissively by the regular dotenv loader."""
    env_file = tmp_path / ".env"
    env_file.write_text("FOO\n", encoding="utf-8")
    service = EnvironmentFileService(resolved_env_files=[env_file])
    item = await service.read_async(file_id=(await service.list_async())[0].id)
    content = "FOO\n=MALFORMED\nBAR=value\n"

    updated = await service.update_async(
        file_id=item.id,
        content=content,
        expected_version=item.version or "",
    )

    assert updated.content == content
    assert env_file.read_text(encoding="utf-8") == content


async def test_environment_file_service_rejects_inline_materialized_source(tmp_path: Path) -> None:
    """Test deployment-secret materialized files are exposed as read-only."""
    env_file = tmp_path / ".env"
    env_file.write_text("VALUE=before\n", encoding="utf-8")
    reason = "Update the deployment secret instead."
    service = EnvironmentFileService(
        resolved_env_files=[env_file],
        read_only_file_sources={env_file: reason},
    )
    item = await service.read_async(file_id=(await service.list_async())[0].id)

    with pytest.raises(ValueError, match="deployment secret"):
        await service.update_async(
            file_id=item.id,
            content="VALUE=after\n",
            expected_version=item.version or "",
        )

    assert item.read_only is True
    assert item.read_only_reason == reason
    assert env_file.read_text(encoding="utf-8") == "VALUE=before\n"


async def test_environment_file_service_deduplicates_sources_in_order(tmp_path: Path) -> None:
    """Test repeated source configuration produces one stable list entry."""
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first_secret = "https://vault.vault.azure.net/secrets/first"
    second_secret = "https://vault.vault.azure.net/secrets/second"
    service = EnvironmentFileService(
        resolved_env_files=[first, first, second],
        env_akv_ref=[first_secret, first_secret, second_secret],
    )

    items = await service.list_async()

    assert [item.path for item in items] == [first_secret, second_secret, str(first), str(second)]
    assert len({item.id for item in items}) == 4


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
                    ("AKV=before\n", "https://vault.vault.azure.net"),
                    ("AKV=after\n", "https://vault.vault.azure.net"),
                ]
            ),
        ) as fetch_mock,
        patch(
            "pyrit.backend.services.environment_file_service._update_akv_document_async",
            new=AsyncMock(return_value="AKV=after\n"),
        ) as update_mock,
        patch(
            "pyrit.backend.services.environment_file_service._validate_dotenv_document",
            return_value="AKV=after\n",
        ) as validate_mock,
    ):
        items = await service.list_async()
        fetch_mock.assert_not_awaited()
        akv_id = items[0].id
        loaded = await service.read_async(file_id=akv_id)
        updated = await service.update_async(
            file_id=akv_id,
            content="AKV=after\n",
            expected_version=loaded.version or "",
        )
        loaded_after_update = await service.read_async(file_id=akv_id)

    assert items[0].id.startswith("akv-")
    assert items[1].id.startswith("file-")
    assert items[0].name == "AKV: bootstrap"
    assert items[0].path == secret_url
    assert items[0].content == ""
    assert loaded.content == "AKV=before\n"
    assert loaded_after_update.content == "AKV=after\n"
    assert fetch_mock.await_count == 3
    assert updated.content == "AKV=after\n"
    validate_mock.assert_called_once_with("AKV=after\n", strict=True, silent=True)
    update_mock.assert_awaited_once_with(secret_url=secret_url, content="AKV=after\n")


async def test_environment_file_service_rejects_versioned_akv_update() -> None:
    """Test that editing cannot silently bypass a pinned AKV secret version."""
    with patch("azure.identity.aio.DefaultAzureCredential") as credential_mock:
        with pytest.raises(ValueError, match="read-only"):
            await _update_akv_document_async(
                secret_url="https://vault.vault.azure.net/secrets/bootstrap/version-1",
                content="AKV=after\n",
            )

    credential_mock.assert_not_called()


async def test_environment_file_service_updates_unversioned_akv_document() -> None:
    """Test persisting an unversioned Key Vault document."""
    secret_url = "https://vault.vault.azure.net/secrets/bootstrap"
    credential = MagicMock()
    credential.__aenter__ = AsyncMock(return_value=credential)
    credential.__aexit__ = AsyncMock(return_value=None)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.set_secret = AsyncMock()

    with (
        patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
        patch("pyrit.backend.services.environment_file_service._create_akv_secret_client", return_value=client),
    ):
        result = await _update_akv_document_async(secret_url=secret_url, content="AKV=validated\n")

    assert result == "AKV=validated\n"
    client.set_secret.assert_awaited_once_with("bootstrap", "AKV=validated\n")


async def test_environment_file_service_sanitizes_akv_content_before_write() -> None:
    """Test non-strict AKV validation occurs before persistence."""
    secret_url = "https://vault.vault.azure.net/secrets/bootstrap"
    service = EnvironmentFileService(resolved_env_files=[], env_akv_ref=[secret_url], env_akv_strict=False)
    file_id = (await service.list_async())[0].id

    with patch(
        "pyrit.backend.services.environment_file_service._update_akv_document_async",
        new=AsyncMock(return_value="VALID=value\n"),
    ) as update_mock:
        result = await service._write_source_async(file_id=file_id, content="INVALID\nVALID=value\n")

    assert result.content == "VALID=value\n"
    update_mock.assert_awaited_once_with(secret_url=secret_url, content="VALID=value\n")


async def test_environment_file_service_reads_latest_akv_content() -> None:
    """Test that every read observes the current AKV content."""
    secret_url = "https://vault.vault.azure.net/secrets/bootstrap"
    service = EnvironmentFileService(resolved_env_files=[], env_akv_ref=[secret_url])
    file_id = (await service.list_async())[0].id
    with patch(
        "pyrit.backend.services.environment_file_service._fetch_akv_document_async",
        new=AsyncMock(side_effect=[("FIRST=1\n", "vault"), ("SECOND=2\n", "vault")]),
    ) as fetch_mock:
        assert (await service.read_async(file_id=file_id)).content == "FIRST=1\n"
        assert (await service.read_async(file_id=file_id)).content == "SECOND=2\n"

    assert fetch_mock.await_count == 2


@pytest.mark.parametrize("file_id", ["akv-invalid", "akv-missing", "file-unknown"])
def test_environment_file_service_rejects_invalid_akv_id(file_id: str) -> None:
    """Test malformed, out-of-range, and negative Key Vault identifiers."""
    service = EnvironmentFileService(
        resolved_env_files=[], env_akv_ref=["https://vault.vault.azure.net/secrets/bootstrap"]
    )

    with pytest.raises(KeyError, match=file_id):
        service._get_akv_source(file_id)


@pytest.mark.parametrize("file_id", ["invalid", "file-missing", "akv-unknown"])
def test_environment_file_service_rejects_invalid_file_id(file_id: str, tmp_path: Path) -> None:
    """Test malformed, out-of-range, and negative local file identifiers."""
    service = EnvironmentFileService(resolved_env_files=[tmp_path / ".env"])

    with pytest.raises(KeyError, match=file_id):
        service._get_file_path(file_id)


async def test_environment_file_service_rejects_stale_update(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("VALUE=before\n", encoding="utf-8")
    service = EnvironmentFileService(resolved_env_files=[env_file])
    file_id = (await service.list_async())[0].id
    loaded = await service.read_async(file_id=file_id)
    env_file.write_text("VALUE=external\n", encoding="utf-8")

    with pytest.raises(EnvironmentFileConflictError, match="reload"):
        await service.update_async(
            file_id=file_id,
            content="VALUE=after\n",
            expected_version=loaded.version or "",
        )

    assert env_file.read_text(encoding="utf-8") == "VALUE=external\n"
