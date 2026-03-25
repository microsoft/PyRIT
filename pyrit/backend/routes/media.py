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
    requested = Path(path).resolve()

    # Determine allowed directory from memory results_path
    try:
        memory = CentralMemory.get_memory_instance()
        allowed_root = Path(memory.results_path).resolve()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Memory not initialized; cannot determine results path.") from exc

    # Path traversal guard
    if not requested.is_relative_to(allowed_root):
        raise HTTPException(status_code=403, detail="Access denied: path is outside the allowed results directory.")

    # Restrict to known media subdirectories (e.g. prompt-memory-entries/)
    relative = requested.relative_to(allowed_root)
    if not relative.parts or relative.parts[0] not in _ALLOWED_SUBDIRECTORIES:
        raise HTTPException(status_code=403, detail="Access denied: path is not in a media subdirectory.")

    # Only allow known media file extensions
    if requested.suffix.lower() not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=403, detail="Access denied: file type is not allowed.")

    if not requested.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    mime_type, _ = mimetypes.guess_type(str(requested))
    return FileResponse(
        path=str(requested),
        media_type=mime_type or "application/octet-stream",
    )
