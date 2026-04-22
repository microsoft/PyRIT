# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from pyrit.models.storage_io import (
    AzureBlobStorageIO,
    DiskStorageIO,
    SupportedContentType,
)


@pytest.fixture
def azure_blob_storage_io():
    """Fixture to create an instance of AzureBlobStorageIO."""
    return AzureBlobStorageIO(container_url="dummy")


@pytest.mark.asyncio
async def test_disk_storage_io_read_file():
    storage = DiskStorageIO()
    path = "sample.txt"
    content = b"Test content"

    with patch("aiofiles.open", new_callable=MagicMock) as mock_open:
        mock_file = mock_open.return_value.__aenter__.return_value
        mock_file.read = AsyncMock(return_value=content)

        result = await storage.read_file(path)
        assert result == content
        mock_open.assert_called_once_with(Path(path), "rb")


@pytest.mark.asyncio
async def test_disk_storage_io_write_file():
    storage = DiskStorageIO()
    path = "sample.txt"
    content = b"Test content"

    with patch("aiofiles.open", new_callable=MagicMock) as mock_open:
        mock_file = mock_open.return_value.__aenter__.return_value
        mock_file.write = AsyncMock()

        await storage.write_file(path, content)
        mock_open.assert_called_once_with(Path(path), "wb")
        mock_file.write.assert_called_once_with(content)


@pytest.mark.asyncio
async def test_disk_storage_io_path_exists():
    storage = DiskStorageIO()
    path = "sample.txt"

    with patch("os.path.exists", return_value=True) as mock_exists:
        result = await storage.path_exists(path)
        assert result is True
        mock_exists.assert_called_once_with(Path(path))


@pytest.mark.asyncio
async def test_disk_storage_io_is_file():
    storage = DiskStorageIO()
    path = "sample.txt"

    with patch("os.path.isfile", return_value=True) as mock_isfile:
        result = await storage.is_file(path)
        assert result is True
        mock_isfile.assert_called_once_with(Path(path))


@pytest.mark.asyncio
async def test_disk_storage_io_create_directory_if_not_exists():
    storage = DiskStorageIO()
    directory_path = "sample_dir"

    with patch("os.makedirs") as mock_mkdir, patch("pathlib.Path.exists", return_value=False) as mock_exists:
        await storage.create_directory_if_not_exists(directory_path)
        mock_exists.assert_called_once()
        mock_mkdir.assert_called_once_with(Path(directory_path), exist_ok=True)


@pytest.mark.asyncio
async def test_azure_blob_storage_io_read_file(azure_blob_storage_io):
    azure_blob_storage_io._client_async = AsyncMock()  # Use Mock since get_blob_client is sync

    mock_blob_client = AsyncMock()
    mock_blob_stream = AsyncMock()

    azure_blob_storage_io._client_async.get_blob_client = Mock(return_value=mock_blob_client)
    mock_blob_client.download_blob = AsyncMock(return_value=mock_blob_stream)
    mock_blob_stream.readall = AsyncMock(return_value=b"Test file content")
    azure_blob_storage_io._client_async.close = AsyncMock()

    result = await azure_blob_storage_io.read_file(
        "https://account.blob.core.windows.net/container/dir1/dir2/sample.png"
    )

    assert result == b"Test file content"


@pytest.mark.asyncio
async def test_azure_blob_storage_io_read_file_with_relative_path(azure_blob_storage_io):
    mock_container_client = AsyncMock()
    azure_blob_storage_io._client_async = mock_container_client

    mock_blob_client = AsyncMock()
    mock_blob_stream = AsyncMock()

    mock_container_client.get_blob_client = Mock(return_value=mock_blob_client)
    mock_blob_client.download_blob = AsyncMock(return_value=mock_blob_stream)
    mock_blob_stream.readall = AsyncMock(return_value=b"Test file content")
    mock_container_client.close = AsyncMock()

    result = await azure_blob_storage_io.read_file("dir1/dir2/sample.png")

    assert result == b"Test file content"
    mock_container_client.get_blob_client.assert_called_once_with(blob="dir1/dir2/sample.png")


@pytest.mark.asyncio
async def test_azure_blob_storage_io_write_file():
    container_url = "https://youraccount.blob.core.windows.net/yourcontainer"
    azure_blob_storage_io = AzureBlobStorageIO(
        container_url=container_url, blob_content_type=SupportedContentType.PLAIN_TEXT
    )

    mock_blob_client = AsyncMock()
    mock_container_client = AsyncMock()

    mock_blob_client.upload_blob = AsyncMock()

    mock_container_client.get_blob_client.return_value = mock_blob_client

    with patch.object(azure_blob_storage_io, "_create_container_client_async", return_value=None):
        azure_blob_storage_io._client_async = mock_container_client
        azure_blob_storage_io._upload_blob_async = AsyncMock()

        data_to_write = b"Test data"
        path = "https://youraccount.blob.core.windows.net/yourcontainer/testfile.txt"

        await azure_blob_storage_io.write_file(path, data_to_write)

        azure_blob_storage_io._upload_blob_async.assert_awaited_with(
            file_name="testfile.txt", data=data_to_write, content_type=SupportedContentType.PLAIN_TEXT.value
        )


@pytest.mark.asyncio
async def test_azure_blob_storage_io_write_file_with_relative_path():
    container_url = "https://youraccount.blob.core.windows.net/yourcontainer"
    azure_blob_storage_io = AzureBlobStorageIO(
        container_url=container_url, blob_content_type=SupportedContentType.PLAIN_TEXT
    )

    mock_container_client = AsyncMock()

    with patch.object(azure_blob_storage_io, "_create_container_client_async", return_value=None):
        azure_blob_storage_io._client_async = mock_container_client
        azure_blob_storage_io._upload_blob_async = AsyncMock()

        data_to_write = b"Test data"
        await azure_blob_storage_io.write_file("dir1/dir2/testfile.txt", data_to_write)

        azure_blob_storage_io._upload_blob_async.assert_awaited_with(
            file_name="dir1/dir2/testfile.txt",
            data=data_to_write,
            content_type=SupportedContentType.PLAIN_TEXT.value,
        )


@pytest.mark.asyncio
async def test_azure_blob_storage_io_create_container_client_uses_explicit_sas_token():
    container_url = "https://youraccount.blob.core.windows.net/yourcontainer"
    sas_token = "explicit-sas-token"
    azure_blob_storage_io = AzureBlobStorageIO(container_url=container_url, sas_token=sas_token)

    mock_container_client = AsyncMock()

    with (
        patch("pyrit.models.storage_io.AzureStorageAuth.get_sas_token", new_callable=AsyncMock) as mock_get_sas_token,
        patch(
            "pyrit.models.storage_io.AsyncContainerClient.from_container_url", return_value=mock_container_client
        ) as mock_from_container_url,
    ):
        await azure_blob_storage_io._create_container_client_async()

    mock_get_sas_token.assert_not_awaited()
    mock_from_container_url.assert_called_once_with(container_url=container_url, credential=sas_token)
    assert azure_blob_storage_io._client_async is mock_container_client


@pytest.mark.asyncio
async def test_azure_storage_io_path_exists(azure_blob_storage_io):
    azure_blob_storage_io._client_async = AsyncMock()

    mock_blob_client = AsyncMock()

    azure_blob_storage_io._client_async.get_blob_client = Mock(return_value=mock_blob_client)
    mock_blob_client.get_blob_properties = AsyncMock()
    azure_blob_storage_io._client_async.close = AsyncMock()
    file_path = "https://example.blob.core.windows.net/container/dir1/dir2/blob_name.txt"
    exists = await azure_blob_storage_io.path_exists(file_path)
    assert exists is True


@pytest.mark.asyncio
async def test_azure_storage_io_path_exists_with_relative_path(azure_blob_storage_io):
    mock_container_client = AsyncMock()
    azure_blob_storage_io._client_async = mock_container_client

    mock_blob_client = AsyncMock()

    mock_container_client.get_blob_client = Mock(return_value=mock_blob_client)
    mock_blob_client.get_blob_properties = AsyncMock()
    mock_container_client.close = AsyncMock()

    exists = await azure_blob_storage_io.path_exists("dir1/dir2/blob_name.txt")

    assert exists is True
    mock_container_client.get_blob_client.assert_called_once_with(blob="dir1/dir2/blob_name.txt")


@pytest.mark.asyncio
async def test_azure_storage_io_is_file(azure_blob_storage_io):
    azure_blob_storage_io._client_async = AsyncMock()

    mock_blob_client = AsyncMock()

    azure_blob_storage_io._client_async.get_blob_client = Mock(return_value=mock_blob_client)
    mock_blob_properties = Mock(size=1024)
    mock_blob_client.get_blob_properties = AsyncMock(return_value=mock_blob_properties)
    azure_blob_storage_io._client_async.close = AsyncMock()
    file_path = "https://example.blob.core.windows.net/container/dir1/dir2/blob_name.txt"
    is_file = await azure_blob_storage_io.is_file(file_path)
    assert is_file is True


@pytest.mark.asyncio
async def test_azure_storage_io_is_file_with_relative_path(azure_blob_storage_io):
    mock_container_client = AsyncMock()
    azure_blob_storage_io._client_async = mock_container_client

    mock_blob_client = AsyncMock()

    mock_container_client.get_blob_client = Mock(return_value=mock_blob_client)
    mock_blob_properties = Mock(size=1024)
    mock_blob_client.get_blob_properties = AsyncMock(return_value=mock_blob_properties)
    mock_container_client.close = AsyncMock()

    is_file = await azure_blob_storage_io.is_file("dir1/dir2/blob_name.txt")

    assert is_file is True
    mock_container_client.get_blob_client.assert_called_once_with(blob="dir1/dir2/blob_name.txt")


def test_azure_storage_io_parse_blob_url_valid(azure_blob_storage_io):
    file_path = "https://example.blob.core.windows.net/container/dir1/dir2/blob_name.txt"
    container_name, blob_name = azure_blob_storage_io.parse_blob_url(file_path)

    assert container_name == "container"
    assert blob_name == "dir1/dir2/blob_name.txt"


def test_azure_storage_io_parse_blob_url_invalid(azure_blob_storage_io):
    with pytest.raises(ValueError, match="Invalid blob URL"):
        azure_blob_storage_io.parse_blob_url("invalid_url")


def test_azure_storage_io_parse_blob_url_without_scheme(azure_blob_storage_io):
    with pytest.raises(ValueError, match="Invalid blob URL"):
        azure_blob_storage_io.parse_blob_url("example.blob.core.windows.net/container/dir1/blob_name.txt")


def test_azure_storage_io_parse_blob_url_without_netloc(azure_blob_storage_io):
    with pytest.raises(ValueError, match="Invalid blob URL"):
        azure_blob_storage_io.parse_blob_url("https:///container/dir1/blob_name.txt")


def test_resolve_blob_name_with_full_url(azure_blob_storage_io):
    result = azure_blob_storage_io._resolve_blob_name("https://account.blob.core.windows.net/container/dir1/file.txt")
    assert result == "dir1/file.txt"


def test_resolve_blob_name_with_relative_path(azure_blob_storage_io):
    assert azure_blob_storage_io._resolve_blob_name("dir1/dir2/file.txt") == "dir1/dir2/file.txt"


def test_resolve_blob_name_with_simple_filename(azure_blob_storage_io):
    assert azure_blob_storage_io._resolve_blob_name("file.txt") == "file.txt"


def test_resolve_blob_name_normalizes_backslashes(azure_blob_storage_io):
    assert azure_blob_storage_io._resolve_blob_name("dir1\\dir2\\file.txt") == "dir1/dir2/file.txt"


def test_resolve_blob_name_with_path_object(azure_blob_storage_io):
    from pathlib import PurePosixPath

    result = azure_blob_storage_io._resolve_blob_name(PurePosixPath("dir1/dir2/file.txt"))
    assert result == "dir1/dir2/file.txt"


@pytest.mark.asyncio
async def test_upload_blob_raises_when_client_async_none():
    obj = AzureBlobStorageIO.__new__(AzureBlobStorageIO)
    obj._client_async = None
    with pytest.raises(RuntimeError, match="Azure container client not initialized"):
        await obj._upload_blob_async(file_name="test.txt", data=b"data", content_type="text/plain")


@pytest.mark.asyncio
async def test_read_file_lazy_initializes_client(azure_blob_storage_io):
    mock_container_client = AsyncMock()
    mock_blob_client = AsyncMock()
    mock_blob_stream = AsyncMock()

    mock_container_client.get_blob_client = Mock(return_value=mock_blob_client)
    mock_blob_client.download_blob = AsyncMock(return_value=mock_blob_stream)
    mock_blob_stream.readall = AsyncMock(return_value=b"content")
    mock_container_client.close = AsyncMock()

    azure_blob_storage_io._client_async = None
    with patch.object(
        azure_blob_storage_io,
        "_create_container_client_async",
        new_callable=AsyncMock,
        return_value=mock_container_client,
    ) as mock_create:
        result = await azure_blob_storage_io.read_file("dir1/file.txt")

    mock_create.assert_called_once()
    assert result == b"content"


@pytest.mark.asyncio
async def test_write_file_lazy_initializes_client(azure_blob_storage_io):
    mock_container_client = AsyncMock()
    mock_container_client.close = AsyncMock()

    azure_blob_storage_io._client_async = None
    with patch.object(
        azure_blob_storage_io,
        "_create_container_client_async",
        new_callable=AsyncMock,
        return_value=mock_container_client,
    ) as mock_create:
        azure_blob_storage_io._upload_blob_async = AsyncMock()
        await azure_blob_storage_io.write_file("dir1/file.txt", b"data")

    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_path_exists_lazy_initializes_client(azure_blob_storage_io):
    mock_container_client = AsyncMock()
    mock_blob_client = AsyncMock()

    mock_container_client.get_blob_client = Mock(return_value=mock_blob_client)
    mock_blob_client.get_blob_properties = AsyncMock()
    mock_container_client.close = AsyncMock()

    azure_blob_storage_io._client_async = None
    with patch.object(
        azure_blob_storage_io,
        "_create_container_client_async",
        new_callable=AsyncMock,
        return_value=mock_container_client,
    ) as mock_create:
        result = await azure_blob_storage_io.path_exists("dir1/file.txt")

    mock_create.assert_called_once()
    assert result is True


@pytest.mark.asyncio
async def test_is_file_lazy_initializes_client(azure_blob_storage_io):
    mock_container_client = AsyncMock()
    mock_blob_client = AsyncMock()
    mock_blob_properties = Mock(size=512)

    mock_container_client.get_blob_client = Mock(return_value=mock_blob_client)
    mock_blob_client.get_blob_properties = AsyncMock(return_value=mock_blob_properties)
    mock_container_client.close = AsyncMock()

    azure_blob_storage_io._client_async = None
    with patch.object(
        azure_blob_storage_io,
        "_create_container_client_async",
        new_callable=AsyncMock,
        return_value=mock_container_client,
    ) as mock_create:
        result = await azure_blob_storage_io.is_file("dir1/file.txt")

    mock_create.assert_called_once()
    assert result is True
