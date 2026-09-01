# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read, update, and locally materialize the backend configuration file."""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import aiofiles
import yaml
from azure.core.exceptions import ResourceNotFoundError

from pyrit.common.azure_storage import has_sas_signature, is_azure_blob_uri, redact_url_credentials
from pyrit.setup.configuration_loader import ConfigurationLoader


class ConfigurationFileConflictError(Exception):
    """The configuration source changed after it was read."""


def _configuration_version(*, content: str, exists: bool = True) -> str:
    """
    Create an opaque version token from configuration source state.

    Returns:
        str: The source-state version token.
    """
    return hashlib.sha256(f"{int(exists)}\0{content}".encode()).hexdigest()


def _is_azure_blob_uri(value: str) -> bool:
    """Return whether a value is an Azure Blob Storage HTTPS URI."""
    return is_azure_blob_uri(value, min_path_segments=2)


async def _download_blob_config_async(blob_uri: str) -> bytes:
    """
    Download configuration content from an Azure Blob Storage URI.

    Returns:
        bytes: The downloaded configuration content.
    """
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobClient

    if has_sas_signature(blob_uri):
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

    if has_sas_signature(blob_uri):
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


def _validate_configuration_content(content: str) -> None:
    """Validate YAML configuration content without executing initializers."""
    try:
        yaml_data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML configuration: {exc}") from exc
    if not isinstance(yaml_data, dict):
        raise ValueError("Configuration content must be a non-empty YAML mapping.")
    try:
        ConfigurationLoader.from_dict(yaml_data)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid configuration: {exc}") from exc


def _replace_local_config_file(*, path: Path, content: str) -> None:
    """Atomically replace a local configuration file."""
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class ConfigurationFileService:
    """Manage the configuration source used by the backend."""

    def __init__(self, *, config_file_value: str | None) -> None:
        """Initialize the service with an explicit source or the default configuration path."""
        self._config_file_value = config_file_value
        self._source = config_file_value or str(ConfigurationLoader.get_default_config_path())
        self._update_lock = asyncio.Lock()

    @property
    def source(self) -> str:
        """Configuration source without blob credentials."""
        if _is_azure_blob_uri(self._source):
            return redact_url_credentials(self._source)
        return self._source

    async def read_async(self) -> str:
        """
        Read the current configuration contents.

        Returns:
            str: The UTF-8 configuration contents.
        """
        return await self._read_source_async()

    async def read_with_version_async(self) -> tuple[str, str]:
        """
        Read current content and its version, treating a missing source as empty.

        Returns:
            tuple[str, str]: Current content and opaque source-state version.
        """
        content, exists = await self._read_state_async()
        return content, _configuration_version(content=content, exists=exists)

    async def _read_state_async(self) -> tuple[str, bool]:
        """
        Read the current source state without failing when it does not exist.

        Returns:
            tuple[str, bool]: Current content and whether the source exists.
        """
        try:
            return await self._read_source_async(), True
        except (FileNotFoundError, ResourceNotFoundError):
            return "", False

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

    async def update_async(self, content: str, *, expected_version: str) -> str:
        """
        Replace the current configuration contents if its version is unchanged.

        Returns:
            str: The version token for the persisted content.
        """
        _validate_configuration_content(content)
        async with self._update_lock:
            current_content, exists = await self._read_state_async()
            current_version = _configuration_version(content=current_content, exists=exists)
            if current_version != expected_version:
                raise ConfigurationFileConflictError("Configuration changed; reload it before saving")
            await self._write_source_async(content)
        return _configuration_version(content=content)

    async def _write_source_async(self, content: str) -> str:
        """
        Persist configuration content.

        Returns:
            str: The persisted configuration content.
        """
        if _is_azure_blob_uri(self._source):
            await _upload_blob_config_async(blob_uri=self._source, content=content.encode("utf-8"))
        else:
            await asyncio.to_thread(_replace_local_config_file, path=Path(self._source), content=content)
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
