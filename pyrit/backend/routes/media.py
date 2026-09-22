# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Media file serving endpoint.

Serves locally stored media files (images, audio, video, etc.) via HTTP
so the frontend can reference them by URL instead of requiring inline
base64 data URIs.  For Azure deployments, media is served directly from
Azure Blob Storage via signed URLs and this endpoint is not used.

This route is the only place PyRIT hands stored bytes to a browser, so it controls
whether the browser renders or downloads them. Storage and download support stay
unrestricted on purpose: any file type is a legitimate attack payload. Only
explicitly allowlisted media types render inline; every other type downloads as
opaque bytes.
"""

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from pyrit.memory import CentralMemory

logger = logging.getLogger(__name__)

router = APIRouter()

# Only serve files from known media subdirectories under results_path.
_ALLOWED_SUBDIRECTORIES = {"prompt-memory-entries", "seed-prompt-entries"}

# Only these known-safe media types render inline. Every other extension is
# served as an application/octet-stream attachment.
_INLINE_EXTENSIONS = {
    # Images
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".ico",
    ".tiff",
    # Audio
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
    ".aac",
    ".m4a",
    # Video
    ".mp4",
    ".webm",
    ".mov",
    ".avi",
    ".mkv",
}


def _validate_media_path(*, path: str, allowed_root: Path) -> Path:
    """
    Validate and sanitize a user-provided file path against an allowed root directory.

    Uses ``Path.resolve()`` to resolve symlinks and ``..`` components, then
    verifies the canonical path is under the allowed root. This is the standard
    sanitization pattern recognized by static analysis tools (e.g. CodeQL
    ``py/path-injection``).

    Args:
        path: The user-provided file path to validate.
        allowed_root: The canonical (``resolve``-d) allowed root directory.

    Returns:
        The canonical, validated file path.

    Raises:
        HTTPException 403: If the path fails any validation check.
    """
    real_path = Path(path).resolve(strict=False)

    try:
        relative_parts = real_path.relative_to(allowed_root).parts
    except ValueError as exc:
        raise HTTPException(
            status_code=403, detail="Access denied: path is outside the allowed results directory."
        ) from exc

    # Restrict to known media subdirectories (e.g. prompt-memory-entries/)
    if not relative_parts or relative_parts[0] not in _ALLOWED_SUBDIRECTORIES:
        raise HTTPException(status_code=403, detail="Access denied: path is not in a media subdirectory.")

    return real_path


@router.get("/media")
async def serve_media_async(
    path: str = Query(..., description="Absolute path to the local media file to serve."),
) -> FileResponse:
    """
    Serve a locally stored media file.

    The file path must reside under a known media subdirectory within the
    configured results directory (e.g. ``dbdata/prompt-memory-entries/``)
    to prevent path traversal attacks and exfiltration of sensitive files.

    Upload storage and downloads accept any file type. Extensions in
    ``_INLINE_EXTENSIONS`` use their inferred media type and can render inline.
    Every other extension is returned as an ``application/octet-stream``
    attachment with ``nosniff`` so the browser downloads rather than renders it.

    Args:
        path: Absolute path to the file.

    Returns:
        FileResponse with the file content and inferred MIME type.

    Raises:
        HTTPException 403: If the path is outside the allowed directory.
        HTTPException 404: If the file does not exist.
        HTTPException 500: If memory is not initialized.
    """
    try:
        memory = CentralMemory.get_memory_instance()
        if not memory.results_path:
            raise HTTPException(status_code=500, detail="Memory results_path is not configured.")
        allowed_root = Path(memory.results_path).resolve(strict=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Memory not initialized; cannot determine results path.") from exc

    validated_path = _validate_media_path(path=path, allowed_root=allowed_root)

    if not validated_path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    extension = validated_path.suffix.lower()
    render_inline = extension in _INLINE_EXTENSIONS
    guessed_type, _ = mimetypes.guess_type(validated_path) if render_inline else (None, None)
    return FileResponse(
        path=validated_path,
        media_type=guessed_type or "application/octet-stream",
        filename=None if render_inline else validated_path.name,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )
