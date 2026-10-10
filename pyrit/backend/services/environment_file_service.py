# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read and update environment files selected by backend configuration."""

import asyncio
import os
import tempfile
from hashlib import sha256
from pathlib import Path

import aiofiles

from pyrit.backend.models.configuration import EnvironmentFileContent
from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH
from pyrit.setup.environment_loading import (
    _create_akv_secret_client,
    _fetch_akv_document_async,
    _parse_akv_secret_url,
    _validate_dotenv_document,
)


class EnvironmentFileConflictError(Exception):
    """The environment source changed after it was read."""


def _source_id(*, kind: str, source: str) -> str:
    """
    Create a stable opaque identifier from an environment source.

    Returns:
        str: The prefixed source digest.
    """
    return f"{kind}-{sha256(source.encode('utf-8')).hexdigest()}"


def _content_version(*, content: str, exists: bool = True) -> str:
    """
    Create an opaque version token from source state without exposing content.

    Returns:
        str: The source-state digest.
    """
    state = f"{int(exists)}\0{content}".encode()
    return sha256(state).hexdigest()


def _replace_file(*, path: Path, content: str) -> None:
    """Atomically replace a local secret file with owner-only permissions where supported."""
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary_path = Path(temporary_name)
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, mode="w", encoding="utf-8", newline="") as environment_file:
            descriptor = -1
            environment_file.write(content)
            environment_file.flush()
            os.fsync(environment_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


async def _update_akv_document_async(*, secret_url: str, content: str) -> str:
    """
    Persist a new current version of an AKV bootstrap dotenv secret.

    Returns:
        str: The content persisted to Key Vault.
    """
    from azure.identity.aio import DefaultAzureCredential

    vault_url, secret_name, secret_version = _parse_akv_secret_url(secret_url)
    if secret_version is not None:
        raise ValueError("Versioned Azure Key Vault environment sources are read-only")

    async with DefaultAzureCredential() as credential:
        async with _create_akv_secret_client(vault_url=vault_url, credential=credential) as client:
            await client.set_secret(secret_name, content)
    return content


class EnvironmentFileService:
    """Manage environment files using PyRIT's resolved loading semantics."""

    def __init__(
        self,
        *,
        resolved_env_files: list[Path] | None,
        env_akv_ref: list[str] | None = None,
        env_akv_strict: bool = True,
        read_only_file_sources: dict[Path, str] | None = None,
    ) -> None:
        """Initialize from ``ConfigurationLoader.resolve_env_files()`` output."""
        self._env_akv_strict = env_akv_strict
        paths = (
            [CONFIGURATION_DIRECTORY_PATH / ".env", CONFIGURATION_DIRECTORY_PATH / ".env.local"]
            if resolved_env_files is None
            else resolved_env_files
        )
        self._akv_sources = {_source_id(kind="akv", source=secret_url): secret_url for secret_url in env_akv_ref or []}
        self._file_sources = {_source_id(kind="file", source=str(path)): path for path in paths}
        self._read_only_file_sources = read_only_file_sources or {}
        self._update_locks: dict[str, asyncio.Lock] = {}

    async def list_async(self) -> list[EnvironmentFileContent]:
        """
        List configured environment sources in load order without reading contents.

        Returns:
            list[EnvironmentFileContent]: Configured environment source metadata.
        """
        items: list[EnvironmentFileContent] = []
        for file_id, secret_url in self._akv_sources.items():
            _, secret_name, _ = _parse_akv_secret_url(secret_url)
            items.append(
                EnvironmentFileContent(
                    id=file_id,
                    name=f"AKV: {secret_name}",
                    path=secret_url,
                    content="",
                    exists=True,
                )
            )

        exists_values = await asyncio.gather(*(asyncio.to_thread(path.exists) for path in self._file_sources.values()))
        for (file_id, path), exists in zip(self._file_sources.items(), exists_values, strict=True):
            read_only_reason = self._read_only_file_sources.get(path)
            items.append(
                EnvironmentFileContent(
                    id=file_id,
                    name=path.name,
                    path=str(path),
                    content="",
                    exists=exists,
                    read_only=read_only_reason is not None,
                    read_only_reason=read_only_reason,
                )
            )
        return items

    async def read_async(self, *, file_id: str) -> EnvironmentFileContent:
        """
        Read one configured environment source.

        Returns:
            EnvironmentFileContent: The selected source and its current contents.

        Raises:
            KeyError: If the identifier does not name a configured environment source.
        """
        if file_id in self._akv_sources:
            return await self._read_akv_source_async(file_id=file_id)
        return await self._read_file_source_async(file_id=file_id)

    async def update_async(self, *, file_id: str, content: str, expected_version: str) -> EnvironmentFileContent:
        """
        Replace one configured environment file by its stable identifier.

        Returns:
            EnvironmentFileContent: The updated environment file.

        Raises:
            KeyError: If the identifier does not name a configured environment file.
        """
        if file_id not in self._akv_sources and file_id not in self._file_sources:
            raise KeyError(file_id)
        if read_only_reason := self._get_read_only_reason(file_id):
            raise ValueError(read_only_reason)

        lock = self._update_locks.setdefault(file_id, asyncio.Lock())
        async with lock:
            current = await self.read_async(file_id=file_id)
            if current.version != expected_version:
                raise EnvironmentFileConflictError("Environment source changed; reload it before saving")
            return await self._write_source_async(file_id=file_id, content=content)

    async def _read_akv_source_async(self, *, file_id: str) -> EnvironmentFileContent:
        secret_url = self._get_akv_source(file_id)
        content, _ = await _fetch_akv_document_async(
            secret_url=secret_url,
            strict=self._env_akv_strict,
            silent=True,
        )
        _, secret_name, _ = _parse_akv_secret_url(secret_url)
        return EnvironmentFileContent(
            id=file_id,
            name=f"AKV: {secret_name}",
            path=secret_url,
            content=content,
            exists=True,
            version=_content_version(content=content),
        )

    async def _read_file_source_async(self, *, file_id: str) -> EnvironmentFileContent:
        path = self._get_file_path(file_id)
        read_only_reason = self._read_only_file_sources.get(path)
        exists = await asyncio.to_thread(path.exists)
        content = ""
        if exists:
            async with aiofiles.open(path, encoding="utf-8") as environment_file:
                content = await environment_file.read()
        return EnvironmentFileContent(
            id=file_id,
            name=path.name,
            path=str(path),
            content=content,
            exists=exists,
            version=_content_version(content=content, exists=exists),
            read_only=read_only_reason is not None,
            read_only_reason=read_only_reason,
        )

    async def _write_source_async(self, *, file_id: str, content: str) -> EnvironmentFileContent:
        if file_id in self._akv_sources:
            validated_content = _validate_dotenv_document(
                content,
                strict=self._env_akv_strict,
                silent=True,
            )
            return await self._write_akv_source_async(file_id=file_id, content=validated_content)
        path = self._get_file_path(file_id)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(_replace_file, path=path, content=content)
        return EnvironmentFileContent(
            id=file_id,
            name=path.name,
            path=str(path),
            content=content,
            exists=True,
            version=_content_version(content=content),
        )

    async def _write_akv_source_async(self, *, file_id: str, content: str) -> EnvironmentFileContent:
        secret_url = self._get_akv_source(file_id)
        persisted_content = await _update_akv_document_async(
            secret_url=secret_url,
            content=content,
        )
        _, secret_name, _ = _parse_akv_secret_url(secret_url)
        return EnvironmentFileContent(
            id=file_id,
            name=f"AKV: {secret_name}",
            path=secret_url,
            content=persisted_content,
            exists=True,
            version=_content_version(content=persisted_content),
        )

    def _get_akv_source(self, file_id: str) -> str:
        try:
            return self._akv_sources[file_id]
        except KeyError:
            raise KeyError(file_id) from None

    def _get_file_path(self, file_id: str) -> Path:
        try:
            return self._file_sources[file_id]
        except KeyError:
            raise KeyError(file_id) from None

    def _get_read_only_reason(self, file_id: str) -> str | None:
        """Return the configured read-only reason for a local source."""
        path = self._file_sources.get(file_id)
        return self._read_only_file_sources.get(path) if path else None
