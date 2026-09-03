# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for custom initializer script storage."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pyrit.registry.custom_initializer_storage import CustomInitializerStorage


@pytest.mark.parametrize(
    "source",
    [
        "https://account.blob.attacker.example/initializers",
        "https://user@account.blob.core.windows.net/initializers",
        "https://account.blob.core.windows.net:8443/initializers",
        "https://blob.core.windows.net/initializers",
    ],
)
def test_blob_storage_rejects_untrusted_authorities(source: str) -> None:
    """Test rejecting Blob lookalikes before Azure credentials are acquired."""
    with pytest.raises(ValueError, match="local directory or Azure Blob container URI"):
        CustomInitializerStorage(source=source)


def test_local_storage_lists_saves_and_deletes_scripts(tmp_path: Path) -> None:
    """Test local directory storage lifecycle."""
    storage = CustomInitializerStorage(source=str(tmp_path))

    storage.save_script(name="example", content="VALUE = 1\n")

    assert storage.list_scripts() == {"example": "VALUE = 1\n"}
    storage.delete_script("example")
    assert storage.list_scripts() == {}


def test_blob_storage_uses_sas_and_ignores_non_python_blobs() -> None:
    """Test container storage operations with a SAS-authenticated URI."""
    client = MagicMock()
    client.__enter__.return_value = client
    client.list_blobs.return_value = [
        SimpleNamespace(name="first.py"),
        SimpleNamespace(name="notes.txt"),
        SimpleNamespace(name="archive/ignored.py"),
    ]
    client.download_blob.return_value.readall.return_value = b"VALUE = 1\n"
    source = "https://account.blob.core.windows.net/initializers?sp=rwd&sig=secret"
    storage = CustomInitializerStorage(source=source)

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client) as client_factory:
        assert storage.list_scripts() == {"first": "VALUE = 1\n"}
        storage.save_script(name="second", content="VALUE = 2\n")
        storage.delete_script("second")

    assert storage.display_source == "https://account.blob.core.windows.net/initializers"
    client_factory.assert_called_with(container_url=source)
    client.list_blobs.assert_called_once_with()
    client.upload_blob.assert_called_once_with(name="second.py", data=b"VALUE = 2\n", overwrite=True)
    client.delete_blob.assert_called_once_with("second.py")


def test_blob_storage_scopes_operations_to_prefix() -> None:
    """Test storage operations under a virtual directory prefix."""
    client = MagicMock()
    client.__enter__.return_value = client
    client.list_blobs.return_value = [
        SimpleNamespace(name="custom-initializers/first.py"),
        SimpleNamespace(name="custom-initializers/notes.txt"),
        SimpleNamespace(name="custom-initializers/archive/ignored.py"),
    ]
    client.download_blob.return_value.readall.return_value = b"VALUE = 1\n"
    source = "https://account.blob.core.windows.net/copyrit/custom-initializers?sp=rwd&sig=secret"
    container_url = "https://account.blob.core.windows.net/copyrit?sp=rwd&sig=secret"
    storage = CustomInitializerStorage(source=source)

    with patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client) as client_factory:
        assert storage.list_scripts() == {"first": "VALUE = 1\n"}
        storage.save_script(name="second", content="VALUE = 2\n")
        storage.delete_script("second")

    assert storage.display_source == "https://account.blob.core.windows.net/copyrit/custom-initializers"
    assert storage.get_script_source("first") == (
        "https://account.blob.core.windows.net/copyrit/custom-initializers/first.py"
    )
    client_factory.assert_called_with(container_url=container_url)
    client.list_blobs.assert_called_once_with(name_starts_with="custom-initializers/")
    client.download_blob.assert_called_once_with("custom-initializers/first.py")
    client.upload_blob.assert_called_once_with(
        name="custom-initializers/second.py",
        data=b"VALUE = 2\n",
        overwrite=True,
    )
    client.delete_blob.assert_called_once_with("custom-initializers/second.py")


def test_blob_storage_uses_default_credential_without_sas() -> None:
    """Test container storage authentication without a SAS token."""
    credential = MagicMock()
    credential.__enter__.return_value = credential
    client = MagicMock()
    client.__enter__.return_value = client
    source = "https://account.blob.core.windows.net/initializers"
    storage = CustomInitializerStorage(source=source)

    with (
        patch("azure.identity.DefaultAzureCredential", return_value=credential) as credential_factory,
        patch("azure.storage.blob.ContainerClient.from_container_url", return_value=client) as client_factory,
    ):
        storage.save_script(name="example", content="VALUE = 1\n")

    credential_factory.assert_called_once_with()
    client_factory.assert_called_once_with(container_url=source, credential=credential)


def test_local_storage_returns_script_path(tmp_path: Path) -> None:
    """Test resolving the displayed path for a local script."""
    storage = CustomInitializerStorage(source=str(tmp_path))

    assert storage.get_script_source("example") == str(tmp_path / "example.py")
    assert storage.display_source == str(tmp_path)


def test_local_storage_reads_latest_script_content(tmp_path: Path) -> None:
    """Test that every listing observes current script content."""
    script_path = tmp_path / "example.py"
    script_path.write_text("VALUE = 1\n", encoding="utf-8")
    storage = CustomInitializerStorage(source=str(tmp_path))

    assert storage.list_scripts() == {"example": "VALUE = 1\n"}
    script_path.write_text("VALUE = 2\n", encoding="utf-8")
    assert storage.list_scripts() == {"example": "VALUE = 2\n"}


def test_direct_python_blob_rejects_name_outside_prefix() -> None:
    """Test that blobs outside the configured virtual directory are ignored."""
    assert not CustomInitializerStorage._is_direct_python_blob(
        blob_name="other/example.py", prefix="custom-initializers/"
    )


def test_local_storage_cannot_open_blob_client(tmp_path: Path) -> None:
    """Test that local storage cannot create an Azure container client."""
    storage = CustomInitializerStorage(source=str(tmp_path))

    with pytest.raises(RuntimeError, match="not configured"):
        with storage._open_container_client():
            pass
