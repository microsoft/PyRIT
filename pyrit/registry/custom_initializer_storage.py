# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Storage backends for custom initializer Python source."""

from __future__ import annotations

from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from pyrit.common.azure_storage import has_sas_signature, is_azure_blob_uri, redact_url_credentials

if TYPE_CHECKING:
    from collections.abc import Generator

    from azure.storage.blob import ContainerClient


class CustomInitializerStorage:
    """Read and write custom initializer scripts in a directory or blob container."""

    def __init__(self, *, source: str) -> None:
        """
        Initialize storage from a local directory or Azure Blob source URI.

        Raises:
            ValueError: If the source has an unsupported URI scheme.
        """
        self._source = source
        self._is_blob = is_azure_blob_uri(source)
        if not self._is_blob and urlparse(source).scheme and not Path(source).drive:
            raise ValueError(
                "Custom initializer source must be a local directory or Azure Blob container URI "
                "with an optional blob prefix"
            )
        self._container_url, self._blob_prefix = self._parse_blob_source() if self._is_blob else (None, "")

    @property
    def display_source(self) -> str:
        """Storage source without Azure Blob credentials."""
        if not self._is_blob:
            return self._source
        return redact_url_credentials(self._source)

    def get_script_source(self, name: str) -> str:
        """
        Get the credential-free location of a custom initializer script.

        Returns:
            str: Local file path or Azure Blob URI for the script.
        """
        if self._is_blob:
            return f"{self.display_source.rstrip('/')}/{name}.py"
        return str(Path(self._source).expanduser() / f"{name}.py")

    def list_scripts(self) -> dict[str, str]:
        """
        List stored Python scripts by registry name.

        Returns:
            dict[str, str]: Script content keyed by registry name.
        """
        if self._is_blob:
            return self._list_blob_scripts()

        directory = Path(self._source).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        return {path.stem: path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.py"))}

    def save_script(self, *, name: str, content: str) -> None:
        """Persist one custom initializer script."""
        if self._is_blob:
            with self._open_container_client() as client:
                client.upload_blob(name=self._get_blob_name(name), data=content.encode("utf-8"), overwrite=True)
        else:
            directory = Path(self._source).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{name}.py").write_text(content, encoding="utf-8")

    def delete_script(self, name: str) -> None:
        """Delete one custom initializer script if it exists."""
        if self._is_blob:
            from azure.core.exceptions import ResourceNotFoundError

            with self._open_container_client() as client:
                with suppress(ResourceNotFoundError):
                    client.delete_blob(self._get_blob_name(name))
        else:
            (Path(self._source).expanduser() / f"{name}.py").unlink(missing_ok=True)

    def _list_blob_scripts(self) -> dict[str, str]:
        """
        Read Python scripts from the configured Azure Blob container.

        Returns:
            dict[str, str]: Script content keyed by blob stem.
        """
        scripts: dict[str, str] = {}
        with self._open_container_client() as client:
            prefix = f"{self._blob_prefix}/" if self._blob_prefix else None
            blobs = client.list_blobs(name_starts_with=prefix) if prefix else client.list_blobs()
            blob_names = sorted(
                blob.name for blob in blobs if self._is_direct_python_blob(blob_name=blob.name, prefix=prefix)
            )
            for blob_name in blob_names:
                relative_name = blob_name.removeprefix(prefix or "")
                scripts[PurePosixPath(relative_name).stem] = client.download_blob(blob_name).readall().decode("utf-8")
        return scripts

    def _parse_blob_source(self) -> tuple[str, str]:
        """
        Split the configured source into a container URL and blob prefix.

        Returns:
            tuple[str, str]: The container URL and decoded blob prefix.
        """
        parsed_uri = urlparse(self._source)
        container_path, _, prefix = parsed_uri.path.strip("/").partition("/")
        container_url = parsed_uri._replace(path=f"/{container_path}", fragment="").geturl()
        return container_url, unquote(prefix).strip("/")

    def _get_blob_name(self, name: str) -> str:
        """
        Build the blob name for a registry entry.

        Returns:
            str: The prefixed Python blob name.
        """
        file_name = f"{name}.py"
        return f"{self._blob_prefix}/{file_name}" if self._blob_prefix else file_name

    @staticmethod
    def _is_direct_python_blob(*, blob_name: str, prefix: str | None) -> bool:
        """Return whether a blob is a direct Python child of the configured prefix."""
        if prefix and not blob_name.startswith(prefix):
            return False
        relative_name = blob_name.removeprefix(prefix or "")
        return "/" not in relative_name and PurePosixPath(relative_name).suffix == ".py"

    @contextmanager
    def _open_container_client(self) -> Generator[ContainerClient, None, None]:
        """
        Yield an Azure Blob container client and close its credential.

        Yields:
            ContainerClient: A client scoped to the configured container.

        Raises:
            RuntimeError: If called for a non-Blob source.
        """
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        if self._container_url is None:
            raise RuntimeError("Azure Blob container URL is not configured")

        if has_sas_signature(self._container_url):
            with ContainerClient.from_container_url(container_url=self._container_url) as client:
                yield client
            return

        with DefaultAzureCredential() as credential:
            with ContainerClient.from_container_url(
                container_url=self._container_url,
                credential=credential,
            ) as client:
                yield client
