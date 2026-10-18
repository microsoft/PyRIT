# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared classification and persistence for backend media values."""

from __future__ import annotations

import asyncio
import base64
import binascii
import mimetypes
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

from pyrit.backend.models import DEFAULT_MEDIA_EXTENSIONS
from pyrit.memory import AzureBlobStorageIO, CentralMemory, data_serializer_factory
from pyrit.models import MEDIA_PATH_DATA_TYPES

if TYPE_CHECKING:
    from pyrit.backend.models.attacks import MessagePieceRequest
    from pyrit.models import PromptDataType


_ALLOWED_MEDIA_SUBDIRECTORIES = frozenset({"prompt-memory-entries", "seed-prompt-entries"})


class MediaAccessDeniedError(ValueError):
    """A media path or URL is outside the API's allowed storage directories."""


class MediaOrigin(str, Enum):
    """Origin recognized for one path-typed media value."""

    REMOTE_URL = "remote_url"
    MEDIA_REFERENCE = "media_reference"
    LOCAL_PATH = "local_path"
    DATA_URI = "data_uri"
    RAW_BASE64 = "raw_base64"


@dataclass(frozen=True)
class MediaPersistenceResult:
    """Resolved media value returned without mutating the caller's DTO."""

    value: str
    origin: MediaOrigin
    persisted: bool
    resolved: bool
    mime_type: str | None = None
    extension: str | None = None


SerializerFactory = Callable[..., Any]


def validate_media_path(*, path: str, allowed_root: Path) -> Path:
    """
    Resolve symlinks and parent components before checking results-directory containment.

    Args:
        path (str): The local media path.
        allowed_root (Path): The configured results directory.

    Returns:
        The canonical path in an allowed media subdirectory.

    Raises:
        MediaAccessDeniedError: If the path is outside the allowed media directories.
    """
    real_path = Path(path).resolve(strict=False)
    try:
        relative_parts = real_path.relative_to(allowed_root.resolve(strict=False)).parts
    except ValueError as exc:
        raise MediaAccessDeniedError("Access denied: path is outside the allowed results directory.") from exc

    if not relative_parts or relative_parts[0] not in _ALLOWED_MEDIA_SUBDIRECTORIES:
        raise MediaAccessDeniedError("Access denied: path is not in a media subdirectory.")

    return real_path


async def validate_local_media_path_async(*, path: str) -> Path:
    """
    Validate a local media path against the configured results directory.

    Args:
        path (str): The local media path.

    Returns:
        The canonical path in an allowed media subdirectory.

    Raises:
        RuntimeError: If memory or its results path is not configured.
        MediaAccessDeniedError: If the path is outside the allowed media directories.
    """
    try:
        memory = CentralMemory.get_memory_instance()
    except ValueError as exc:
        raise RuntimeError("Memory not initialized; cannot determine results path.") from exc
    if not memory.results_path:
        raise RuntimeError("Memory results_path is not configured.")
    return await asyncio.to_thread(validate_media_path, path=path, allowed_root=Path(memory.results_path))


def results_container_url() -> str | None:
    """
    Return the results container URL when result media is stored in Azure Blob Storage.

    Returns:
        str | None: The container URL, or None for local storage or when memory is not initialized.
    """
    try:
        memory = CentralMemory.get_memory_instance()
    except ValueError:
        return None
    if not memory.results_path or not isinstance(memory.results_storage_io, AzureBlobStorageIO):
        return None
    return str(memory.results_path)


def _has_container_origin(*, url: ParseResult, container: ParseResult) -> bool:
    """
    Return whether a media URL has the results container's host and port, and no user info.

    An omitted HTTPS port is 443, so ``host`` and ``host:443`` match. A port that does not
    parse on either URL never matches.

    Args:
        url (ParseResult): The parsed media URL.
        container (ParseResult): The parsed results container URL.

    Returns:
        bool: True when both URLs name the same host and port and the media URL has no user info.
    """
    try:
        url_port = 443 if url.port is None else url.port
        container_port = 443 if container.port is None else container.port
    except ValueError:
        return False
    return (
        url.username is None
        and url.password is None
        and url.hostname is not None
        and url.hostname == container.hostname
        and url_port == container_port
    )


def validate_blob_media_url(*, url: str, container_url: str) -> str:
    """
    Check that a media URL names a file in a media subdirectory of the results container.

    Blob storage reads a URL's file name from the configured container whatever account or
    container the URL names, so only URLs in that container can refer to managed media.

    Args:
        url (str): The media URL.
        container_url (str): The configured results container URL.

    Returns:
        str: The URL without a query string or fragment.

    Raises:
        MediaAccessDeniedError: If the URL is outside the media subdirectories of the results container.
    """
    parsed = urlparse(url)
    container = urlparse(container_url)
    container_root = container.path.rstrip("/")
    container_path = f"{container_root}/"
    # Stored media URLs are the configured container URL plus "/<media folder>/...", so they repeat its trailing "/".
    relative_path = parsed.path.removeprefix(container_path).removeprefix(container.path[len(container_root) :])
    segments = [unquote(segment) for segment in relative_path.split("/")]
    if not (
        parsed.scheme == "https"
        and _has_container_origin(url=parsed, container=container)
        and parsed.path.startswith(container_path)
        and len(segments) > 1
        and segments[0] in _ALLOWED_MEDIA_SUBDIRECTORIES
        and all(segment not in {"", ".", ".."} and "/" not in segment and "\\" not in segment for segment in segments)
    ):
        raise MediaAccessDeniedError("Access denied: URL is not in a media subdirectory of the results container.")
    return parsed._replace(query="", fragment="").geturl()


def _is_raw_base64(value: str) -> bool:
    """Return whether *value* is syntactically valid raw base64."""
    try:
        base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def _data_uri_parts(value: str) -> tuple[str | None, str]:
    """Return the optional MIME type and payload from a data URI."""
    header, _, payload = value.partition(",")
    media_type = header.removeprefix("data:").split(";", 1)[0] or None
    return media_type, payload


def _resolve_extension(
    *,
    data_type: PromptDataType,
    mime_type: str | None,
    data_uri_mime_type: str | None,
    use_data_uri_mime_type: bool,
) -> str:
    """
    Resolve the persisted extension using the service's existing policy.

    Returns:
        The extension, including its leading dot.
    """
    extension = mimetypes.guess_extension(mime_type, strict=False) if mime_type else None
    if not extension and use_data_uri_mime_type and data_uri_mime_type:
        extension = mimetypes.guess_extension(data_uri_mime_type, strict=False)
    return extension or DEFAULT_MEDIA_EXTENSIONS.get(str(data_type), ".bin")


async def persist_media_value_async(
    *,
    value: str,
    data_type: PromptDataType,
    mime_type: str | None = None,
    use_data_uri_mime_type: bool = True,
    require_valid_base64_after_path_error: bool = False,
    serializer_factory: SerializerFactory = data_serializer_factory,
    created_paths: list[str] | None = None,
) -> MediaPersistenceResult:
    """
    Classify and, when needed, persist one path-typed media value.

    The two policy flags preserve the small historical differences between
    attack ingestion and converter preview while keeping origin detection,
    extension resolution, and persistence in one component.

    Local paths and ``/api/media`` references must be in a media subdirectory
    of the configured results directory, as required by ``GET /api/media``.
    When results are stored in Azure Blob Storage, HTTP(S) URLs must name a file
    in a media subdirectory of the results container.

    Returns:
        A typed result containing the resolved value and persistence metadata.
    """
    if value.startswith(("http://", "https://")):
        container_url = results_container_url()
        return MediaPersistenceResult(
            value=validate_blob_media_url(url=value, container_url=container_url) if container_url else value,
            origin=MediaOrigin.REMOTE_URL,
            persisted=False,
            resolved=True,
            mime_type=mime_type,
        )

    if value.startswith("/api/media"):
        parsed = urlparse(value)
        if parsed.path == "/api/media":
            file_path = parse_qs(parsed.query).get("path", [None])[0]
            if file_path is None:
                raise ValueError("Media reference must include a path.")
            return MediaPersistenceResult(
                value=str(await validate_local_media_path_async(path=file_path)),
                origin=MediaOrigin.MEDIA_REFERENCE,
                persisted=False,
                resolved=True,
                mime_type=mime_type,
            )

    data_uri_mime_type: str | None = None
    payload = value
    origin = MediaOrigin.RAW_BASE64
    if value.startswith("data:"):
        data_uri_mime_type, payload = _data_uri_parts(value)
        origin = MediaOrigin.DATA_URI
    else:
        try:
            is_file = await asyncio.to_thread(Path(value).is_file)
        except (OSError, ValueError):
            if require_valid_base64_after_path_error and not _is_raw_base64(value):
                raise
            is_file = False
        if is_file:
            return MediaPersistenceResult(
                value=str(await validate_local_media_path_async(path=value)),
                origin=MediaOrigin.LOCAL_PATH,
                persisted=False,
                resolved=True,
                mime_type=mime_type,
            )

    extension = _resolve_extension(
        data_type=data_type,
        mime_type=mime_type,
        data_uri_mime_type=data_uri_mime_type,
        use_data_uri_mime_type=use_data_uri_mime_type,
    )
    serializer = serializer_factory(
        category="prompt-memory-entries",
        data_type=data_type,
        extension=extension,
    )
    if created_paths is None:
        await serializer.save_b64_image_async(data=payload)
    else:
        # Record ownership before writing so partial writes can also be removed.
        created_paths.append(str(await serializer.get_data_filename_async()))
        write_task = asyncio.create_task(serializer.save_b64_image_async(data=payload))
        try:
            await asyncio.shield(write_task)
        except asyncio.CancelledError:
            await write_task
            raise
    return MediaPersistenceResult(
        value=str(serializer.value),
        origin=origin,
        persisted=True,
        resolved=True,
        mime_type=mime_type or data_uri_mime_type,
        extension=extension,
    )


async def persist_message_pieces_async(
    *,
    pieces: Sequence[MessagePieceRequest],
    persisted_paths: list[str] | None = None,
    serializer_factory: SerializerFactory = data_serializer_factory,
) -> None:
    """
    Resolve original and converted media independently, updating values in-place.

    The frontend sends binary media (images, audio, etc.) as base64 strings
    with a ``*_path`` data_type.  The PyRIT target layer expects ``*_path``
    values to be **file paths**, so we decode the base64 data, write it to
    the results store, and replace the request values with the resulting
    file path before the message is built.

    If the value is already an HTTP(S) URL (e.g. an Azure Blob Storage URL
    from a remixed/copied message), it is kept since the file already exists in
    storage. With Azure Blob results storage, the URL must name a file in the
    results container's media subdirectories.
    """
    for piece in pieces:
        original_value = piece.original_value
        converted_value = piece.converted_value
        converted_type = piece.converted_value_data_type or piece.data_type
        if piece.data_type in MEDIA_PATH_DATA_TYPES:
            result = await persist_media_value_async(
                value=original_value,
                data_type=piece.data_type,
                mime_type=piece.mime_type,
                serializer_factory=serializer_factory,
                created_paths=persisted_paths,
            )
            if result.resolved:
                original_value = result.value
                if converted_value is None or (
                    converted_value == piece.original_value and converted_type == piece.data_type
                ):
                    converted_value = original_value

        if (
            converted_value is not None
            and converted_type in MEDIA_PATH_DATA_TYPES
            and (converted_value != original_value or converted_type != piece.data_type)
        ):
            result = await persist_media_value_async(
                value=converted_value,
                data_type=converted_type,
                serializer_factory=serializer_factory,
                created_paths=persisted_paths,
            )
            if result.resolved:
                converted_value = result.value

        piece.original_value = original_value
        piece.converted_value = converted_value
