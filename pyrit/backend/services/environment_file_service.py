# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read and update environment files selected by backend configuration."""

import asyncio
import logging
import time
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

_CACHE_TTL_SECONDS = 10.0
logger = logging.getLogger(__name__)


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
        self._content_cache: dict[str, tuple[float, EnvironmentFileContent]] = {}
        self._cache_lock = asyncio.Lock()
        self._refresh_tasks: dict[str, asyncio.Task[None]] = {}

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
        cached = self._content_cache.get(file_id)
        if cached is not None:
            if time.monotonic() >= cached[0] and file_id not in self._refresh_tasks:
                self._refresh_tasks[file_id] = asyncio.create_task(self._refresh_in_background_async(file_id=file_id))
            return cached[1].model_copy()

        return await self._refresh_cache_async(file_id=file_id, force=False)

    async def _refresh_cache_async(self, *, file_id: str, force: bool) -> EnvironmentFileContent:
        """
        Read one source and replace its cached content.

        Returns:
            EnvironmentFileContent: The selected source and its current contents.
        """
        async with self._cache_lock:
            cached = self._content_cache.get(file_id)
            if not force and cached is not None:
                return cached[1].model_copy()

            if file_id.startswith("akv:"):
                try:
                    index = int(file_id.removeprefix("akv:"))
                    secret_url = self._akv_refs[index]
                except (ValueError, IndexError):
                    raise KeyError(file_id) from None
                if index < 0:
                    raise KeyError(file_id)

                content, _ = await _fetch_akv_document_async(
                    secret_url=secret_url,
                    strict=self._env_akv_strict,
                    silent=True,
                )
                _, secret_name, _ = _parse_akv_secret_url(secret_url)
                item = EnvironmentFileContent(
                    id=file_id,
                    name=f"AKV: {secret_name}",
                    path=secret_url,
                    content=content,
                    exists=True,
                )
            else:
                try:
                    index = int(file_id)
                    path = self._paths[index]
                except (ValueError, IndexError):
                    raise KeyError(file_id) from None
                if index < 0:
                    raise KeyError(file_id)

                exists = await asyncio.to_thread(path.exists)
                content = ""
                if exists:
                    async with aiofiles.open(path, encoding="utf-8") as environment_file:
                        content = await environment_file.read()
                item = EnvironmentFileContent(
                    id=file_id,
                    name=path.name,
                    path=str(path),
                    content=content,
                    exists=exists,
                )

            self._content_cache[file_id] = (time.monotonic() + _CACHE_TTL_SECONDS, item)
            return item.model_copy()

    async def _refresh_in_background_async(self, *, file_id: str) -> None:
        """Refresh an expired source without delaying the current request."""
        try:
            await self._refresh_cache_async(file_id=file_id, force=True)
        except Exception:
            logger.warning("Failed to refresh environment source %s", file_id, exc_info=True)
        finally:
            self._refresh_tasks.pop(file_id, None)

    async def update_async(self, *, file_id: str, content: str) -> EnvironmentFileContent:
        """
        Replace one configured environment file by its stable identifier.

        Returns:
            EnvironmentFileContent: The updated environment file.

        Raises:
            KeyError: If the identifier does not name a configured environment file.
        """
        async with self._cache_lock:
            if file_id.startswith("akv:"):
                try:
                    index = int(file_id.removeprefix("akv:"))
                    secret_url = self._akv_refs[index]
                except (ValueError, IndexError):
                    raise KeyError(file_id) from None
                if index < 0:
                    raise KeyError(file_id)

                persisted_content = await _update_akv_document_async(
                    secret_url=secret_url,
                    content=content,
                    strict=self._env_akv_strict,
                )
                _, secret_name, _ = _parse_akv_secret_url(secret_url)
                item = EnvironmentFileContent(
                    id=file_id,
                    name=f"AKV: {secret_name}",
                    path=secret_url,
                    content=persisted_content,
                    exists=True,
                )
            else:
                try:
                    index = int(file_id)
                    path = self._paths[index]
                except (ValueError, IndexError):
                    raise KeyError(file_id) from None
                if index < 0:
                    raise KeyError(file_id)

                await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
                async with aiofiles.open(path, mode="w", encoding="utf-8") as environment_file:
                    await environment_file.write(content)
                item = EnvironmentFileContent(
                    id=file_id,
                    name=path.name,
                    path=str(path),
                    content=content,
                    exists=True,
                )

            self._content_cache[file_id] = (time.monotonic() + _CACHE_TTL_SECONDS, item)
            return item.model_copy()
