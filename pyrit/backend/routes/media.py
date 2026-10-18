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

import asyncio
import logging
import mimetypes

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from pyrit.backend.services.media_persistence import MediaAccessDeniedError, validate_local_media_path_async

logger = logging.getLogger(__name__)

router = APIRouter()

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
        HTTPException 500: If memory or its results path is not configured.
    """
    try:
        validated_path = await validate_local_media_path_async(path=path)
    except MediaAccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not await asyncio.to_thread(validated_path.is_file):
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
