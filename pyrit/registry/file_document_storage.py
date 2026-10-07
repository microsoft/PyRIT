# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared local-directory and Azure Blob storage for flat, named documents."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from pyrit.common.azure_storage import has_sas_signature, is_azure_blob_uri, redact_url_credentials
from pyrit.models.identifiers.class_name_utils import validate_registry_name

if TYPE_CHECKING:
    from collections.abc import Generator

    from azure.storage.blob import ContainerClient

logger = logging.getLogger(__name__)

# Both platforms lock a single byte at offset zero, which they allow past the end of an
# empty file. The lock belongs to the open file handle, so it excludes other threads in
# this process as well as other processes, and the kernel drops it if the holder exits.
if sys.platform == "win32":
    import msvcrt

    def _try_acquire_exclusive_lock(descriptor: int) -> bool:
        """
        Try to take the exclusive lock without waiting.

        Returns:
            bool: Whether the lock was taken.
        """
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _release_exclusive_lock(descriptor: int) -> None:
        """Release the exclusive lock held on an open descriptor."""
        os.lseek(descriptor, 0, os.SEEK_SET)
        with suppress(OSError):
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_acquire_exclusive_lock(descriptor: int) -> bool:
        """
        Try to take the exclusive lock without waiting.

        Returns:
            bool: Whether the lock was taken.
        """
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _release_exclusive_lock(descriptor: int) -> None:
        """Release the exclusive lock held on an open descriptor."""
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)


class DocumentConflictError(ValueError):
    """A stored document changed after the caller read it."""

    def __init__(
        self,
        *,
        name: str,
        expected_version: str | None,
        actual_version: str | None,
        label: str = "Document",
    ) -> None:
        """
        Initialize the error with the versions that failed to match.

        Args:
            name (str): The document name the write addressed.
            expected_version (str | None): The version the caller believed was stored.
            actual_version (str | None): The version storage actually held.
            label (str): Human-readable noun for the stored document. Defaults to "Document".
        """
        self.name = name
        self.expected_version = expected_version
        self.actual_version = actual_version
        if actual_version is None:
            detail = "it no longer exists"
        elif expected_version is None:
            detail = "it already exists"
        else:
            detail = "it was changed by someone else"
        super().__init__(f"{label} '{name}' could not be saved because {detail}. Reload it and reapply.")


class FileDocumentStorage:
    """
    Read and write flat, named text documents in a directory or blob container.

    Documents live directly under the configured source, one file per name, with a
    fixed extension. Nested paths are ignored so a virtual directory prefix behaves
    the same way in both backends.

    Every operation that addresses a single document validates the name first. The
    name becomes a path component in both backends, so an unvalidated name would let
    a caller read, overwrite, or delete a file outside the configured source. The
    check lives here rather than in each subclass so no document API can omit it.

    Listing applies the same rule before it reads anything, so every name it returns can
    be passed back to the single-document operations. Names come from the file system
    rather than from a caller, so the source can hold files this class cannot address or
    cannot read; those are skipped with a warning rather than failing the whole listing.

    Documents are read and written as raw bytes. Decoding belongs to the subclasses,
    which know how to report a document they cannot interpret and can skip just that one.
    Bytes also keep what a caller hashes identical to what is stored, which text mode
    would not: it rewrites line endings per platform.

    Local writes stage the content beside the destination and move it into place, so an
    interrupted write leaves the previous document intact rather than truncating it.

    Conditional writes go through ``_save_document_conditional``, which refuses a write
    whose stored state no longer matches the version the caller read. Atomic replacement
    and conditional writing solve different problems: replacement stops a reader seeing a
    half-written document, while the version check stops a second writer silently
    discarding the first writer's edit. Each backend enforces the check with its own
    primitive, so the comparison and the write cannot be separated by another writer:

    - Blob creates upload with ``overwrite=False`` and updates send the ETag read moments
      earlier as an ``If-Match`` precondition, both evaluated by the service.
    - Local writes hold an OS advisory lock on a sibling file for the whole
      read-compare-replace sequence, which serializes every writer that goes through this
      class, including ones in other processes. The kernel owns the lock, so a writer that
      dies releases it rather than stranding the document.

    The local guarantee is cooperative: it binds writers using this class, not someone
    editing the file directly. That case is covered instead by the version itself, which
    hashes stored bytes and so changes under any edit, whoever made it.

    Subclasses supply the extension and a human-readable label for error messages,
    then expose a domain-specific API over the protected document operations.
    """

    LOCK_SUFFIX: str = ".lock"
    LOCK_TIMEOUT_SECONDS: float = 10.0
    LOCK_POLL_SECONDS: float = 0.05

    def __init__(self, *, source: str, extension: str, source_label: str) -> None:
        """
        Initialize storage from a local directory or Azure Blob source URI.

        Args:
            source (str): Local directory path or Azure Blob container URI with an
                optional blob prefix.
            extension (str): File extension including the leading dot, such as ``".py"``.
            source_label (str): Human-readable label naming the stored documents, used
                in error messages.

        Raises:
            ValueError: If the source has an unsupported URI scheme.
        """
        self._source = source
        self._extension = extension
        self._source_label = source_label
        self._is_blob = is_azure_blob_uri(source)
        if not self._is_blob and urlparse(source).scheme and not Path(source).drive:
            raise ValueError(
                f"{source_label} source must be a local directory or Azure Blob container URI "
                "with an optional blob prefix"
            )
        self._container_url, self._blob_prefix = self._parse_blob_source() if self._is_blob else (None, "")

    @property
    def display_source(self) -> str:
        """Storage source without Azure Blob credentials."""
        if not self._is_blob:
            return self._source
        return redact_url_credentials(self._source)

    def _get_document_source(self, name: str) -> str:
        """
        Get the credential-free location of one document.

        Returns:
            str: Local file path or Azure Blob URI for the document.

        Raises:
            ValueError: If *name* is not a legal registry name.
        """
        validate_registry_name(name)
        if self._is_blob:
            return f"{self.display_source.rstrip('/')}/{name}{self._extension}"
        return str(self._local_directory() / f"{name}{self._extension}")

    def _list_documents(self) -> dict[str, bytes]:
        """
        Read every stored document that the single-document operations can address.

        A name is checked before its content is read, so a file this class cannot address
        is never opened and cannot fail the listing on its way out.

        Returns:
            dict[str, bytes]: Document content keyed by name.
        """
        return self._list_blob_documents() if self._is_blob else self._list_local_documents()

    def _list_local_documents(self) -> dict[str, bytes]:
        """
        Read addressable documents from the configured local directory.

        Returns:
            dict[str, bytes]: Document content keyed by file stem.
        """
        directory = self._local_directory(create=True)
        documents: dict[str, bytes] = {}
        for path in sorted(directory.glob(f"*{self._extension}")):
            if not self._is_addressable_name(path.stem):
                continue
            try:
                documents[path.stem] = path.read_bytes()
            except OSError as error:
                logger.warning(f"Skipping unreadable document '{path.name}' in {self.display_source}: {error}")
        return documents

    def _is_addressable_name(self, name: str) -> bool:
        """
        Return whether a discovered document name is one this storage can address.

        Ordinary files such as ``__init__.py`` or ``My-Script.py`` can sit alongside
        valid documents, and a caller that fed such a name back into a read, write, or
        delete would get a ``ValueError`` it has no way to anticipate.

        Returns:
            bool: Whether the name is a legal registry name.
        """
        try:
            validate_registry_name(name)
        except ValueError as error:
            logger.warning(f"Ignoring stored document '{name}{self._extension}' in {self.display_source}: {error}")
            return False
        return True

    def _read_document_bytes(self, name: str) -> bytes | None:
        """
        Read the raw bytes of one document.

        Returns:
            bytes | None: Document content, or ``None`` if it does not exist.

        Raises:
            ValueError: If *name* is not a legal registry name.
        """
        validate_registry_name(name)
        if self._is_blob:
            from azure.core.exceptions import ResourceNotFoundError

            with self._open_container_client() as client:
                try:
                    return client.download_blob(self._get_blob_name(name)).readall()
                except ResourceNotFoundError:
                    return None

        path = self._local_directory() / f"{name}{self._extension}"
        return path.read_bytes() if path.is_file() else None

    def _save_document(self, *, name: str, content: bytes) -> None:
        """
        Persist one document, replacing any existing content.

        Raises:
            ValueError: If *name* is not a legal registry name.
        """
        validate_registry_name(name)
        if self._is_blob:
            with self._open_container_client() as client:
                client.upload_blob(name=self._get_blob_name(name), data=content, overwrite=True)
        else:
            directory = self._local_directory(create=True)
            self._replace_file(path=directory / f"{name}{self._extension}", content=content)

    @staticmethod
    def _compute_version(content: bytes) -> str:
        """
        Create an opaque version token from stored document content.

        The token hashes the stored bytes rather than recording writes made through this
        class, so an edit made by hand or by another tool invalidates it too.

        Returns:
            str: The document-state version token.
        """
        return hashlib.sha256(content).hexdigest()

    def _conflict_error(
        self, *, name: str, expected_version: str | None, actual_version: str | None
    ) -> DocumentConflictError:
        """
        Build the error raised when a conditional write is refused.

        Subclasses override this to surface a domain-specific error type without
        reimplementing the comparison that detects the conflict.

        Returns:
            DocumentConflictError: The error describing the version mismatch.
        """
        return DocumentConflictError(
            name=name,
            expected_version=expected_version,
            actual_version=actual_version,
            label=self._source_label,
        )

    def _save_document_conditional(self, *, name: str, content: bytes, expected_version: str | None) -> str:
        """
        Persist one document only while storage still holds *expected_version*.

        Args:
            name (str): The document name to write.
            content (bytes): The content to store.
            expected_version (str | None): ``None`` to create a document that must not
                already exist, or the version read before the content was edited.

        Returns:
            str: The version token for the newly stored content.

        Raises:
            DocumentConflictError: If the stored version does not match *expected_version*.
            ValueError: If *name* is not a legal registry name.
            TimeoutError: If a local write could not acquire the document lock.
        """
        validate_registry_name(name)
        if self._is_blob:
            return self._save_blob_conditional(name=name, content=content, expected_version=expected_version)
        return self._save_local_conditional(name=name, content=content, expected_version=expected_version)

    def _save_local_conditional(self, *, name: str, content: bytes, expected_version: str | None) -> str:
        """
        Write a local document while holding the lock that covers its version check.

        Returns:
            str: The version token for the newly stored content.

        Raises:
            DocumentConflictError: If the stored version does not match *expected_version*.
            TimeoutError: If the document lock could not be acquired.
        """
        path = self._local_directory(create=True) / f"{name}{self._extension}"
        with self._local_document_lock(path):
            stored = path.read_bytes() if path.is_file() else None
            actual_version = None if stored is None else self._compute_version(stored)
            if actual_version != expected_version:
                raise self._conflict_error(name=name, expected_version=expected_version, actual_version=actual_version)
            self._replace_file(path=path, content=content)
        return self._compute_version(content)

    def _save_blob_conditional(self, *, name: str, content: bytes, expected_version: str | None) -> str:
        """
        Write a blob behind a precondition the service evaluates, not an unconditional overwrite.

        Returns:
            str: The version token for the newly stored content.

        Raises:
            DocumentConflictError: If the stored version does not match *expected_version*.
        """
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError

        blob_name = self._get_blob_name(name)
        with self._open_container_client() as client:
            try:
                if expected_version is None:
                    client.upload_blob(name=blob_name, data=content, overwrite=False)
                else:
                    etag = self._read_unmodified_blob_etag(
                        client=client, blob_name=blob_name, name=name, expected_version=expected_version
                    )
                    client.upload_blob(
                        name=blob_name,
                        data=content,
                        overwrite=True,
                        etag=etag,
                        match_condition=MatchConditions.IfNotModified,
                    )
            except (ResourceExistsError, ResourceModifiedError, ResourceNotFoundError):
                raise self._conflict_error(
                    name=name,
                    expected_version=expected_version,
                    actual_version=self._read_blob_version(client=client, blob_name=blob_name),
                ) from None
        return self._compute_version(content)

    def _read_unmodified_blob_etag(
        self, *, client: ContainerClient, blob_name: str, name: str, expected_version: str
    ) -> str:
        """
        Read the ETag of a blob that still holds *expected_version*.

        The ETag is only a race guard: the version the caller holds is compared first, so
        the public token stays the content hash on both backends.

        Returns:
            str: The ETag to send as the write precondition.

        Raises:
            DocumentConflictError: If the blob is absent or no longer holds that version.
        """
        from azure.core.exceptions import ResourceNotFoundError

        try:
            downloader = client.download_blob(blob_name)
        except ResourceNotFoundError:
            raise self._conflict_error(name=name, expected_version=expected_version, actual_version=None) from None
        actual_version = self._compute_version(downloader.readall())
        if actual_version != expected_version:
            raise self._conflict_error(name=name, expected_version=expected_version, actual_version=actual_version)
        return downloader.properties.etag

    def _read_blob_version(self, *, client: ContainerClient, blob_name: str) -> str | None:
        """
        Read the stored version of one blob.

        Returns:
            str | None: The version token, or ``None`` if the blob does not exist.
        """
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._compute_version(client.download_blob(blob_name).readall())
        except ResourceNotFoundError:
            return None

    @contextmanager
    def _local_document_lock(self, path: Path) -> Generator[None, None, None]:
        """
        Hold an exclusive lock covering one document for the duration of the block.

        The lock is an OS advisory lock taken on a sibling file, so it excludes writers in
        other processes as well as other threads, and the kernel releases it if the holder
        exits without cleaning up. Crash recovery therefore needs no timeout heuristic: a
        lock is held only while its owner is alive. The file itself stays in place because
        it carries no state and deleting it would let two writers hold what they each
        believe is the same lock while the path pointed at different files. It does not
        carry the document extension, so listing never sees it.

        Yields:
            None: Control while the lock is held.

        Raises:
            TimeoutError: If the lock could not be acquired.
        """
        lock_path = path.with_name(f".{path.name}{self.LOCK_SUFFIX}")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            self._acquire_document_lock(descriptor=descriptor, lock_path=lock_path)
            try:
                yield
            finally:
                _release_exclusive_lock(descriptor)
        finally:
            os.close(descriptor)

    def _acquire_document_lock(self, *, descriptor: int, lock_path: Path) -> None:
        """
        Wait for the exclusive lock on an open lock file until the wait budget runs out.

        Args:
            descriptor (int): Open descriptor for the lock file.
            lock_path (Path): Path of the lock file, named in the timeout message.

        Raises:
            TimeoutError: If the lock is still held when the wait budget runs out.
        """
        deadline = time.monotonic() + self.LOCK_TIMEOUT_SECONDS
        while not _try_acquire_exclusive_lock(descriptor):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting to write '{lock_path.name}'; another writer still holds it.")
            time.sleep(self.LOCK_POLL_SECONDS)

    @staticmethod
    def _replace_file(*, path: Path, content: bytes) -> None:
        """
        Write *content* to *path* without destroying what is already there on failure.

        Writing in place truncates the destination before the new content lands, so an
        interrupted write would leave the stored document empty and a concurrent reader
        could observe a half-written one. Staging the content in a sibling temporary file
        and moving it over the destination keeps the previous document readable until the
        new one is complete. The temporary file does not carry the document extension, so
        a crash between the two steps cannot leave something that listing would pick up.

        Raises:
            OSError: If the document could not be written.
        """
        descriptor, temporary_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    def _delete_document(self, name: str) -> None:
        """
        Delete one document if it exists.

        Raises:
            ValueError: If *name* is not a legal registry name.
        """
        validate_registry_name(name)
        if self._is_blob:
            from azure.core.exceptions import ResourceNotFoundError

            with self._open_container_client() as client:
                with suppress(ResourceNotFoundError):
                    client.delete_blob(self._get_blob_name(name))
        else:
            (self._local_directory() / f"{name}{self._extension}").unlink(missing_ok=True)

    def _local_directory(self, *, create: bool = False) -> Path:
        """
        Resolve the configured local directory.

        Returns:
            Path: The expanded directory path.
        """
        directory = Path(self._source).expanduser()
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _list_blob_documents(self) -> dict[str, bytes]:
        """
        Read addressable documents from the configured Azure Blob container.

        Returns:
            dict[str, bytes]: Document content keyed by blob stem.
        """
        from azure.core.exceptions import AzureError

        documents: dict[str, bytes] = {}
        with self._open_container_client() as client:
            prefix = f"{self._blob_prefix}/" if self._blob_prefix else None
            blobs = client.list_blobs(name_starts_with=prefix) if prefix else client.list_blobs()
            blob_names = sorted(
                blob.name for blob in blobs if self._is_direct_document_blob(blob_name=blob.name, prefix=prefix)
            )
            for blob_name in blob_names:
                name = PurePosixPath(blob_name.removeprefix(prefix or "")).stem
                if not self._is_addressable_name(name):
                    continue
                try:
                    documents[name] = client.download_blob(blob_name).readall()
                except AzureError as error:
                    logger.warning(f"Skipping unreadable document '{blob_name}' in {self.display_source}: {error}")
        return documents

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
        Build the blob name for one document.

        Returns:
            str: The prefixed blob name.
        """
        file_name = f"{name}{self._extension}"
        return f"{self._blob_prefix}/{file_name}" if self._blob_prefix else file_name

    def _is_direct_document_blob(self, *, blob_name: str, prefix: str | None) -> bool:
        """Return whether a blob is a direct child of the configured prefix with the expected extension."""
        if prefix and not blob_name.startswith(prefix):
            return False
        relative_name = blob_name.removeprefix(prefix or "")
        return "/" not in relative_name and PurePosixPath(relative_name).suffix == self._extension

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
