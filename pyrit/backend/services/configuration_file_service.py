# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read, update, and locally materialize the backend configuration file."""

import asyncio
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import aiofiles

from pyrit.setup.configuration_loader import ConfigurationLoader


def _is_azure_blob_uri(value: str) -> bool:
    """Return whether a value is an Azure Blob Storage HTTPS URI."""
    parsed_uri = urlparse(value)
    return (
        parsed_uri.scheme == "https"
        and parsed_uri.hostname is not None
        and ".blob." in parsed_uri.hostname
        and len(parsed_uri.path.strip("/").split("/")) >= 2
    )


async def _download_blob_config_async(blob_uri: str) -> bytes:
    """
    Download configuration content from an Azure Blob Storage URI.

    Returns:
        bytes: The downloaded configuration content.
    """
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobClient

    parsed_uri = urlparse(blob_uri)
    if "sig" in parse_qs(parsed_uri.query):
        async with BlobClient.from_blob_url(blob_url=blob_uri) as blob_client:
            blob_stream = await blob_client.download_blob()
            return bytes(await blob_stream.readall())

    async with DefaultAzureCredential() as credential:
        async with BlobClient.from_blob_url(blob_url=blob_uri, credential=credential) as blob_client:
            blob_stream = await blob_client.download_blob()
            return bytes(await blob_stream.readall())


async def _upload_blob_config_async(*, blob_uri: str, content: bytes) -> None:
    """Upload configuration content to an Azure Blob Storage URI."""
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobClient

    parsed_uri = urlparse(blob_uri)
    if "sig" in parse_qs(parsed_uri.query):
        async with BlobClient.from_blob_url(blob_url=blob_uri) as blob_client:
            await blob_client.upload_blob(content, overwrite=True)
        return

    async with DefaultAzureCredential() as credential:
        async with BlobClient.from_blob_url(blob_url=blob_uri, credential=credential) as blob_client:
            await blob_client.upload_blob(content, overwrite=True)


def _write_temporary_config_file(*, content: bytes, suffix: str) -> Path:
    """
    Write configuration content to a temporary file.

    Returns:
        Path: The path to the temporary file.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary_file:
        temporary_file.write(content)
        return Path(temporary_file.name)


class ConfigurationFileService:
    """Manage the configuration source used by the backend."""

    def __init__(self, *, config_file_value: str | None) -> None:
        """Initialize the service with an explicit source or the default configuration path."""
        self._config_file_value = config_file_value
        self._source = config_file_value or str(ConfigurationLoader.get_default_config_path())

    @property
    def source(self) -> str:
        """Configuration source without blob credentials."""
        if _is_azure_blob_uri(self._source):
            parsed_uri = urlparse(self._source)
            return parsed_uri._replace(query="", fragment="").geturl()
        return self._source

    async def read_async(self) -> str:
        """
        Read the current configuration contents.

        Returns:
            str: The UTF-8 configuration contents.
        """
        return await self._read_source_async()

    async def _read_source_async(self) -> str:
        """
        Read the source and replace the cached content.

        Returns:
            str: The current configuration contents.
        """
        if _is_azure_blob_uri(self._source):
            return (await _download_blob_config_async(self._source)).decode("utf-8")
        async with aiofiles.open(self._source, encoding="utf-8") as config_file:
            content = await config_file.read()
        assert isinstance(content, str)
        return content

    async def update_async(self, content: str) -> None:
        """Replace the current configuration contents."""
        await self._write_source_async(content)

    async def _write_source_async(self, content: str) -> str:
        """
        Persist configuration content.

        Returns:
            str: The persisted configuration content.
        """
        if _is_azure_blob_uri(self._source):
            await _upload_blob_config_async(blob_uri=self._source, content=content.encode("utf-8"))
        else:
            async with aiofiles.open(self._source, mode="w", encoding="utf-8") as config_file:
                await config_file.write(content)
        return content

    @asynccontextmanager
    async def resolve_async(self) -> AsyncGenerator[Path | None, None]:
        """
        Resolve the configuration source to a local path.

        Yields:
            Path | None: The explicit local configuration path, or None to use loader defaults.
        """
        if self._config_file_value is None:
            yield None
            return

        if not _is_azure_blob_uri(self._source):
            yield Path(self._source)
            return

        content = (await self.read_async()).encode("utf-8")
        suffix = Path(urlparse(self._source).path).suffix
        temporary_path = await asyncio.to_thread(_write_temporary_config_file, content=content, suffix=suffix)
        try:
            yield temporary_path
        finally:
            await asyncio.to_thread(temporary_path.unlink, missing_ok=True)
