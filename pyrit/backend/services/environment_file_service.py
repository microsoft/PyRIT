# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read and update environment files selected by backend configuration."""

import asyncio
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


async def _update_akv_document_async(*, secret_url: str, content: str, strict: bool) -> str:
    """
    Create a new current version of an AKV bootstrap dotenv secret.

    Returns:
        str: The validated content persisted to Key Vault.
    """
    from azure.identity.aio import DefaultAzureCredential

    vault_url, secret_name, secret_version = _parse_akv_secret_url(secret_url)
    if secret_version is not None:
        raise ValueError("Versioned Azure Key Vault environment sources are read-only")

    validated_content = _validate_dotenv_document(content, strict=strict, silent=True)
    async with DefaultAzureCredential() as credential:
        async with _create_akv_secret_client(vault_url=vault_url, credential=credential) as client:
            await client.set_secret(secret_name, validated_content)
    return validated_content


class EnvironmentFileService:
    """Manage environment files using PyRIT's resolved loading semantics."""

    def __init__(
        self,
        *,
        resolved_env_files: list[Path] | None,
        env_akv_ref: list[str] | None = None,
        env_akv_strict: bool = True,
    ) -> None:
        """Initialize from ``ConfigurationLoader.resolve_env_files()`` output."""
        self._akv_refs = env_akv_ref or []
        self._env_akv_strict = env_akv_strict
        self._paths = (
            [CONFIGURATION_DIRECTORY_PATH / ".env", CONFIGURATION_DIRECTORY_PATH / ".env.local"]
            if resolved_env_files is None
            else resolved_env_files
        )

    async def list_async(self) -> list[EnvironmentFileContent]:
        """
        List configured environment sources in load order without reading contents.

        Returns:
            list[EnvironmentFileContent]: Configured environment source metadata.
        """
        items: list[EnvironmentFileContent] = []
        for index, secret_url in enumerate(self._akv_refs):
            _, secret_name, _ = _parse_akv_secret_url(secret_url)
            items.append(
                EnvironmentFileContent(
                    id=f"akv:{index}",
                    name=f"AKV: {secret_name}",
                    path=secret_url,
                    content="",
                    exists=True,
                )
            )

        exists_values = await asyncio.gather(*(asyncio.to_thread(path.exists) for path in self._paths))
        for index, (path, exists) in enumerate(zip(self._paths, exists_values, strict=True)):
            items.append(
                EnvironmentFileContent(
                    id=str(index),
                    name=path.name,
                    path=str(path),
                    content="",
                    exists=exists,
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
        return await self._read_source_async(file_id=file_id)

    async def _read_source_async(self, *, file_id: str) -> EnvironmentFileContent:
        """
        Read one source and replace its cached content.

        Returns:
            EnvironmentFileContent: The selected source and its current contents.
        """
        if file_id.startswith("akv:"):
            return await self._read_akv_source_async(file_id=file_id)
        return await self._read_file_source_async(file_id=file_id)

    async def update_async(self, *, file_id: str, content: str) -> EnvironmentFileContent:
        """
        Replace one configured environment file by its stable identifier.

        Returns:
            EnvironmentFileContent: The updated environment file.

        Raises:
            KeyError: If the identifier does not name a configured environment file.
        """
        return await self._write_source_async(file_id=file_id, content=content)

    async def _read_akv_source_async(self, *, file_id: str) -> EnvironmentFileContent:
        index, secret_url = self._get_akv_source(file_id)
        content, _ = await _fetch_akv_document_async(
            secret_url=secret_url,
            strict=self._env_akv_strict,
            silent=True,
        )
        _, secret_name, _ = _parse_akv_secret_url(secret_url)
        return EnvironmentFileContent(
            id=f"akv:{index}", name=f"AKV: {secret_name}", path=secret_url, content=content, exists=True
        )

    async def _read_file_source_async(self, *, file_id: str) -> EnvironmentFileContent:
        path = self._get_file_path(file_id)
        exists = await asyncio.to_thread(path.exists)
        content = ""
        if exists:
            async with aiofiles.open(path, encoding="utf-8") as environment_file:
                content = await environment_file.read()
        return EnvironmentFileContent(id=file_id, name=path.name, path=str(path), content=content, exists=exists)

    async def _write_source_async(self, *, file_id: str, content: str) -> EnvironmentFileContent:
        if file_id.startswith("akv:"):
            return await self._write_akv_source_async(file_id=file_id, content=content)
        path = self._get_file_path(file_id)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        async with aiofiles.open(path, mode="w", encoding="utf-8") as environment_file:
            await environment_file.write(content)
        return EnvironmentFileContent(id=file_id, name=path.name, path=str(path), content=content, exists=True)

    async def _write_akv_source_async(self, *, file_id: str, content: str) -> EnvironmentFileContent:
        index, secret_url = self._get_akv_source(file_id)
        persisted_content = await _update_akv_document_async(
            secret_url=secret_url,
            content=content,
            strict=self._env_akv_strict,
        )
        _, secret_name, _ = _parse_akv_secret_url(secret_url)
        return EnvironmentFileContent(
            id=f"akv:{index}",
            name=f"AKV: {secret_name}",
            path=secret_url,
            content=persisted_content,
            exists=True,
        )

    def _get_akv_source(self, file_id: str) -> tuple[int, str]:
        try:
            index = int(file_id.removeprefix("akv:"))
            secret_url = self._akv_refs[index]
        except (ValueError, IndexError):
            raise KeyError(file_id) from None
        if index < 0:
            raise KeyError(file_id)
        return index, secret_url

    def _get_file_path(self, file_id: str) -> Path:
        try:
            index = int(file_id)
            path = self._paths[index]
        except (ValueError, IndexError):
            raise KeyError(file_id) from None
        if index < 0:
            raise KeyError(file_id)
        return path
