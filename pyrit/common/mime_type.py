# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from mimetypes import guess_type
from pathlib import Path

_EXTENSION_TO_MIME_TYPE: dict[str, str] = {
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".json": "application/json",
    ".xml": "application/xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".avi": "video/x-msvideo",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".rtf": "application/rtf",
    ".zip": "application/zip",
    ".bin": "application/octet-stream",
}


def get_mime_type(file_path: str | Path) -> str | None:
    """
    Get the MIME type for a file path.

    Explicit PyRIT mappings take precedence over the platform MIME database.

    Args:
        file_path (str | Path): Input file path.

    Returns:
        str | None: MIME type if detectable; otherwise None.
    """
    extension = Path(file_path).suffix.casefold()
    if mime_type := _EXTENSION_TO_MIME_TYPE.get(extension):
        return mime_type

    mime_type, _ = guess_type(file_path, strict=False)
    return mime_type
