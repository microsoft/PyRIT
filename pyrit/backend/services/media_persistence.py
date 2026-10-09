# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared classification and persistence for backend media values."""

from __future__ import annotations

import asyncio
import base64
import binascii
import mimetypes
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

from pyrit.backend.models import DEFAULT_MEDIA_EXTENSIONS
from pyrit.backend.services.media_url_import import (
    download_media_url_async,
    media_content_type,
    media_extension,
    redact_url,
)
from pyrit.common.azure_storage import is_azure_blob_uri, redact_url_credentials
from pyrit.memory import CentralMemory, data_serializer_factory
from pyrit.models import MEDIA_PATH_DATA_TYPES

if TYPE_CHECKING:
    from pyrit.backend.models.attacks import MessagePieceRequest
    from pyrit.models import PromptDataType

# Media is only read from these folders under the memory results path.
MEDIA_SUBDIRECTORIES = frozenset({"prompt-memory-entries", "seed-prompt-entries"})
# Media type families of the path data types; binary_path accepts any content.
_MEDIA_FAMILIES: dict[PromptDataType, str] = {"image_path": "image", "audio_path": "audio", "video_path": "video"}
_CHECKED_FAMILIES = frozenset({"image", "audio", "video", "text"})
# Prompt metadata that records where imported media came from, for the original and converted values.
_MEDIA_SOURCE_PREFIXES = ("media_source", "converted_media_source")
MEDIA_SOURCE_METADATA_KEYS = frozenset(
    f"{prefix}_{field}" for prefix in _MEDIA_SOURCE_PREFIXES for field in ("url", "content_type")
)


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
    source: str | None = None


SerializerFactory = Callable[..., Any]


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


def resolve_managed_media_path(*, path: str, allowed_root: Path) -> Path:
    """
    Return the canonical form of a local media path inside the results directory.

    ``Path.resolve()`` removes ``..`` parts and follows symlinks before the
    containment check, so the returned path is the file that will actually be read.

    Args:
        path (str): The caller-supplied file path.
        allowed_root (Path): The canonical (``resolve``-d) memory results directory.

    Returns:
        Path: The canonical path.

    Raises:
        ValueError: If the path is outside ``allowed_root`` or not in a media folder.
    """
    real_path = Path(path).resolve(strict=False)
    try:
        relative_parts = real_path.relative_to(allowed_root).parts
    except ValueError as exc:
        raise ValueError("Media path is outside the allowed results directory.") from exc
    if not relative_parts or relative_parts[0] not in MEDIA_SUBDIRECTORIES:
        raise ValueError("Media path is not in a media folder of the allowed results directory.")
    return real_path


def _results_root() -> str:
    """Return the memory results path, which is a local folder or a blob container URL."""
    return str(CentralMemory.get_memory_instance().results_path)


def _is_remote_root(root: str) -> bool:
    """Return whether the results path is a blob container URL rather than a local folder."""
    return urlparse(root).scheme in ("http", "https")


def _resolve_local_media_value(value: str) -> Path:
    """
    Resolve a local media path against the configured results folder.

    Returns:
        Path: The canonical media path.

    Raises:
        ValueError: If results are stored remotely or the path is outside managed media.
    """
    root = _results_root()
    if _is_remote_root(root):
        raise ValueError("Local media paths are not accepted when results are stored in Azure Blob Storage.")
    return resolve_managed_media_path(path=value, allowed_root=Path(root).resolve(strict=False))


def _is_media_blob_path(*, path: str, prefix: str) -> bool:
    """
    Return whether a URL path names a blob inside a media folder of the results container.

    Returns:
        bool: True when the path is the results prefix, a media folder, and a blob name without empty or dot
            segments.
    """
    path = path.replace("\\", "/")
    if not path.startswith(prefix):
        return False
    blob_segments = path[len(prefix) :].split("/")
    return (
        len(blob_segments) > 1 and blob_segments[0] in MEDIA_SUBDIRECTORIES and not {"", ".", ".."} & set(blob_segments)
    )


def require_managed_blob_url(value: str) -> None:
    """
    Require a media URL to point into a media folder of the configured results container.

    Raises:
        ValueError: If the URL is not an Azure Blob URL inside managed result storage.
    """
    root = _results_root()
    if not _is_remote_root(root) or not is_azure_blob_uri(value):
        raise ValueError("Media URLs must point to this server's result storage.")
    container, candidate = urlparse(root), urlparse(value.replace("\\", "/"))
    # Media URLs are the results URL followed by a media folder, the way the serializer builds them.
    # The blob storage reader uses the path without percent-decoding it, while HTTP clients decode
    # it first, so both readings must stay inside a media folder.
    prefix = f"{container.path}/"
    if (
        not container.path.strip("/")
        or candidate.netloc.lower() != container.netloc.lower()
        or not all(_is_media_blob_path(path=path, prefix=prefix) for path in (candidate.path, unquote(candidate.path)))
    ):
        raise ValueError("Media URLs must point to this server's result storage.")


def is_managed_blob_url(value: str) -> bool:
    """
    Return whether a URL points into a media folder of the configured results container.

    Returns:
        bool: True when the server can read the URL from its own result storage.
    """
    try:
        require_managed_blob_url(value)
    except ValueError:
        return False
    return True


def _media_reference_path(value: str) -> str | None:
    """
    Return the file path from an ``/api/media?path=...`` reference.

    Returns:
        str | None: The referenced path, or None when the value is not a media reference.

    Raises:
        ValueError: If the reference does not carry exactly one non-empty ``path`` value.
    """
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or parsed.path != "/api/media":
        return None
    paths = parse_qs(parsed.query).get("path", [])
    if len(paths) != 1 or not paths[0]:
        raise ValueError("Media references must include exactly one path.")
    return paths[0]


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


async def _write_owned_media_async(
    *,
    serializer: Any,
    created_paths: list[str] | None,
    write: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    """
    Write one media file, first recording it in ``created_paths`` when given.

    Ownership is recorded before writing so partial writes can also be removed, and a
    cancelled request still waits for the write to finish.
    """
    if created_paths is None:
        await write()
        return
    created_paths.append(str(await serializer.get_data_filename_async()))
    write_task = asyncio.create_task(write())
    try:
        await asyncio.shield(write_task)
    except asyncio.CancelledError:
        await write_task
        raise


async def _import_media_url_async(
    *,
    url: str,
    data_type: PromptDataType,
    mime_type: str | None,
    serializer_factory: SerializerFactory,
    created_paths: list[str] | None,
) -> MediaPersistenceResult:
    """
    Download a media URL once and store the bytes in managed media storage under the declared type.

    A missing or generic reported content type is accepted, since the caller declared the type;
    content of a different media family than the declared type is rejected. Format is resolved
    separately from the response, caller MIME type, or URL suffix; unknown formats use ``.bin``.

    Returns:
        MediaPersistenceResult: The managed reference to the stored copy and its redacted source URL.

    Raises:
        ValueError: If the download fails or the content does not match the declared media type.
    """
    download = await download_media_url_async(url=url)
    content_type = media_content_type(download, mime_type=mime_type)
    expected_family = _MEDIA_FAMILIES.get(data_type)
    family = (content_type or "").split("/", 1)[0]
    if expected_family and family in _CHECKED_FAMILIES and family != expected_family:
        raise ValueError(f"Media URL {redact_url(url)} returned {content_type}, not {expected_family} content.")
    extension = media_extension(download, mime_type=mime_type, default=".bin")
    serializer = serializer_factory(
        category="prompt-memory-entries",
        data_type=data_type,
        extension=extension,
    )
    await _write_owned_media_async(
        serializer=serializer,
        created_paths=created_paths,
        write=lambda: serializer.save_data_async(download.content),
    )
    return MediaPersistenceResult(
        value=str(serializer.value),
        origin=MediaOrigin.REMOTE_URL,
        persisted=True,
        resolved=True,
        mime_type=content_type,
        extension=extension,
        source=redact_url(url),
    )


def _is_read_from_result_storage(url: str) -> bool:
    """
    Return whether the storage layer reads this URL from the server's own blob container.

    The storage layer treats any URL on a ``blob.core.windows.net`` host as Azure storage and reads
    it from the configured container, keeping only the blob path.

    Returns:
        bool: True when reading the URL would read a blob in this server's result storage.
    """
    return "blob.core.windows.net" in urlparse(url).netloc


async def persist_media_value_async(
    *,
    value: str,
    data_type: PromptDataType,
    mime_type: str | None = None,
    use_data_uri_mime_type: bool = True,
    require_valid_base64_after_path_error: bool = False,
    import_url: bool = False,
    serializer_factory: SerializerFactory = data_serializer_factory,
    created_paths: list[str] | None = None,
) -> MediaPersistenceResult:
    """
    Classify and, when needed, persist one path-typed media value.

    The two policy flags preserve the small historical differences between
    attack ingestion and converter preview while keeping origin detection,
    extension resolution, and persistence in one component. Existing files and
    ``/api/media`` references are accepted only when they point into this
    server's media storage, so callers cannot make the server read other files.
    Blob URLs inside this server's result storage are kept as references, without
    their query string, since the server reads them with its own credentials.
    Other http(s) URLs are kept as references, or downloaded once into managed
    storage under the declared type when ``import_url`` is set; Azure Blob URLs
    outside the result storage must be imported, because the storage layer would
    read them from this server's own container.

    Returns:
        A typed result containing the resolved value and persistence metadata.

    Raises:
        ValueError: If the value names a file outside the server's media storage, or a
            URL cannot be imported or must be imported.
    """
    if value.startswith(("http://", "https://")):
        if is_managed_blob_url(value):
            return MediaPersistenceResult(
                value=redact_url_credentials(value),
                origin=MediaOrigin.REMOTE_URL,
                persisted=False,
                resolved=True,
                mime_type=mime_type,
            )
        if import_url:
            return await _import_media_url_async(
                url=value,
                data_type=data_type,
                mime_type=mime_type,
                serializer_factory=serializer_factory,
                created_paths=created_paths,
            )
        if _is_read_from_result_storage(value):
            raise ValueError(
                f"Media URL {redact_url(value)} points to Azure Blob Storage outside this server's result storage. "
                "Set import_url to download it, or send it as a url piece."
            )
        return MediaPersistenceResult(
            value=value,
            origin=MediaOrigin.REMOTE_URL,
            persisted=False,
            resolved=True,
            mime_type=mime_type,
        )

    reference_path = _media_reference_path(value)
    if reference_path is not None:
        managed_path = await asyncio.to_thread(_resolve_local_media_value, reference_path)
        return MediaPersistenceResult(
            value=str(managed_path),
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
            is_local_file = await asyncio.to_thread(Path(value).is_file)
        except (OSError, ValueError):
            if require_valid_base64_after_path_error and not _is_raw_base64(value):
                raise
            is_local_file = False
        if is_local_file:
            managed_path = await asyncio.to_thread(_resolve_local_media_value, value)
            return MediaPersistenceResult(
                value=str(managed_path),
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
    await _write_owned_media_async(
        serializer=serializer,
        created_paths=created_paths,
        write=lambda: serializer.save_b64_image_async(data=payload),
    )
    return MediaPersistenceResult(
        value=str(serializer.value),
        origin=origin,
        persisted=True,
        resolved=True,
        mime_type=mime_type or data_uri_mime_type,
        extension=extension,
    )


def media_source_entries(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    Return the media source entries that persistence recorded in a piece's prompt metadata.

    Returns:
        dict[str, Any]: The entries whose keys are in ``MEDIA_SOURCE_METADATA_KEYS``.
    """
    return {key: value for key, value in (metadata or {}).items() if key in MEDIA_SOURCE_METADATA_KEYS}


def media_source_metadata(result: MediaPersistenceResult, *, prefix: str = "media_source") -> dict[str, str]:
    """
    Return prompt metadata naming where an imported media value came from.

    Args:
        result (MediaPersistenceResult): The persistence result for one value.
        prefix (str): Key prefix, so a piece can record its original and converted sources apart.

    Returns:
        dict[str, str]: The redacted source URL and the resolved content type, or an
            empty dict when the value was not imported.
    """
    if result.source is None:
        return {}
    metadata = {f"{prefix}_url": result.source}
    if result.mime_type:
        metadata[f"{prefix}_content_type"] = result.mime_type
    return metadata


async def persist_message_pieces_async(
    *,
    pieces: Sequence[MessagePieceRequest],
    persisted_paths: list[str] | None = None,
    serializer_factory: SerializerFactory = data_serializer_factory,
) -> None:
    """
    Resolve original and converted media independently, updating values in-place.

    The frontend sends binary media (images, audio, etc.) as base64 strings
    with a ``*_path`` data_type. The PyRIT target layer expects ``*_path``
    values to be **file paths**, so base64 data is written to the results
    store and the request values are replaced with the resulting file path.
    Values that already reference stored media are kept after they are
    checked to be inside this server's media storage. Media URLs stay
    references unless the piece sets ``import_url``; imported values are
    stored under their declared type, and the piece's prompt metadata records
    where they came from. ``url`` pieces are left unchanged.

    Args:
        pieces (Sequence[MessagePieceRequest]): Request pieces to resolve in place.
        persisted_paths (list[str] | None): When given, receives the path of every file
            written for these pieces, so a failed request can remove them.
        serializer_factory (SerializerFactory): Factory used to write new media files.
    """
    for piece in pieces:
        original_value = piece.original_value
        converted_value = piece.converted_value
        converted_type = piece.converted_value_data_type or piece.data_type
        mirrors_original = converted_value is None or (
            converted_value == piece.original_value and converted_type == piece.data_type
        )
        source_metadata: dict[str, str] = {}
        original_resolved = False
        if piece.data_type in MEDIA_PATH_DATA_TYPES:
            result = await persist_media_value_async(
                value=original_value,
                data_type=piece.data_type,
                mime_type=piece.mime_type,
                import_url=piece.import_url,
                serializer_factory=serializer_factory,
                created_paths=persisted_paths,
            )
            if result.resolved:
                original_resolved = True
                original_value = result.value
                source_metadata.update(media_source_metadata(result))
                if mirrors_original:
                    converted_value = original_value

        if (
            converted_value is not None
            and converted_type in MEDIA_PATH_DATA_TYPES
            and not (mirrors_original and original_resolved)
        ):
            result = await persist_media_value_async(
                value=converted_value,
                data_type=converted_type,
                import_url=piece.import_url,
                serializer_factory=serializer_factory,
                created_paths=persisted_paths,
            )
            if result.resolved:
                converted_value = result.value
                source_metadata.update(media_source_metadata(result, prefix="converted_media_source"))

        piece.original_value = original_value
        piece.converted_value = converted_value
        if source_metadata:
            # Without other metadata the message mapper records the piece's mime_type; keep it.
            existing = piece.prompt_metadata or ({"mime_type": piece.mime_type} if piece.mime_type else {})
            piece.prompt_metadata = {**existing, **source_metadata}
