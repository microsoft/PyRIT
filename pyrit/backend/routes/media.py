# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Media file serving endpoint.

Serves locally stored media files (images, audio, video, etc.) via HTTP
so the frontend can reference them by URL instead of requiring inline
base64 data URIs.  For Azure deployments, media is served directly from
Azure Blob Storage via signed URLs and this endpoint is not used.
"""

import logging
import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from pyrit.memory import CentralMemory

logger = logging.getLogger(__name__)

router = APIRouter()

# Only serve files from known media subdirectories under results_path.
_ALLOWED_SUBDIRECTORIES = {"prompt-memory-entries", "seed-prompt-entries"}

# Only serve known media file types (allowlist approach).
_ALLOWED_EXTENSIONS = {
    # Images
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".svg",
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
    # Text / documents
    ".txt",
    ".md",
    ".csv",
    ".pdf",
    ".html",
}


def _validate_media_path(*, path: str, allowed_root: str) -> str:
    """
    Validate and sanitize a user-provided file path against an allowed root directory.

    Uses ``os.path.realpath`` to resolve symlinks and ``..`` components, then
    verifies the canonical path starts with the allowed root prefix. This is
    the standard sanitization pattern recognized by static analysis tools
    (e.g. CodeQL ``py/path-injection``).

    Args:
        path: The user-provided file path to validate.
        allowed_root: The canonical (``realpath``-resolved) allowed root directory.

    Returns:
        The canonical, validated file path.

    Raises:
        HTTPException 403: If the path fails any validation check.
    """
    real_path = os.path.realpath(path)
    allowed_prefix = allowed_root + os.sep

    if not real_path.startswith(allowed_prefix):
        raise HTTPException(status_code=403, detail="Access denied: path is outside the allowed results directory.")

    # Restrict to known media subdirectories (e.g. prompt-memory-entries/)
    relative_parts = Path(os.path.relpath(real_path, allowed_root)).parts
    if not relative_parts or relative_parts[0] not in _ALLOWED_SUBDIRECTORIES:
        raise HTTPException(status_code=403, detail="Access denied: path is not in a media subdirectory.")

    # Only allow known media file extensions
    _, ext = os.path.splitext(real_path)
    if ext.lower() not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=403, detail="Access denied: file type is not allowed.")

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

    Args:
        path: Absolute path to the file.

    Returns:
        FileResponse with the file content and inferred MIME type.

    Raises:
        HTTPException 403: If the path is outside the allowed directory or has a blocked extension.
        HTTPException 404: If the file does not exist.
        HTTPException 500: If memory is not initialized.
    """
    try:
        memory = CentralMemory.get_memory_instance()
        allowed_root = os.path.realpath(memory.results_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Memory not initialized; cannot determine results path.") from exc

    validated_path = _validate_media_path(path=path, allowed_root=allowed_root)

    if not os.path.isfile(validated_path):
        raise HTTPException(status_code=404, detail="File not found.")

    mime_type, _ = mimetypes.guess_type(validated_path)
    return FileResponse(
        path=validated_path,
        media_type=mime_type or "application/octet-stream",
    )
