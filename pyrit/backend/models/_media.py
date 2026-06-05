# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Pure media-presentation helpers shared by the attack response models.

These derive download filenames and MIME types from a message piece's stored
value.  They live here (rather than in the mapper) so the response models can
import them without pulling in the mapper's Azure / I/O dependencies, avoiding a
``models`` ↔ ``mappers`` import cycle.
"""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from pyrit.models import PromptDataType

# Friendly download-filename prefixes per media data type.
_FILENAME_PREFIXES = {
    "image_path": "image",
    "audio_path": "audio",
    "video_path": "video",
    "binary_path": "file",
}

# Fallback extension per prefix when the value carries no usable suffix.
_DEFAULT_EXTENSIONS = {"image": ".png", "audio": ".wav", "video": ".mp4", "file": ".bin"}


def infer_mime_type(*, value: str | None, data_type: PromptDataType) -> str | None:
    """
    Infer a MIME type from a value and its data type.

    Args:
        value: The value (typically a file path for media content).
        data_type: The prompt data type (e.g., ``text``, ``image_path``).

    Returns:
        A MIME type string (e.g., ``image/png``), or ``None`` for text content or
        when the type cannot be determined.
    """
    if not value or data_type == "text":
        return None
    mime_type, _ = mimetypes.guess_type(value)
    return mime_type


def build_filename(*, data_type: str, sha256: str | None, value: str | None) -> str | None:
    """
    Build a human-readable download filename from the data type and hash.

    Produces names like ``image_a1b2c3d4e5f6.png``.  The hash is truncated to 12
    characters for readability and falls back to the file extension from *value*
    when one is available.

    Args:
        data_type: The prompt data type (e.g. ``image_path``).
        sha256: The SHA256 hash of the content, if available.
        value: The original value (path or URL) used to infer the file extension.

    Returns:
        A filename like ``image_a1b2c3d4e5f6.png``, or ``None`` for text-like types.
    """
    prefix = _FILENAME_PREFIXES.get(data_type)
    if not prefix:
        return None

    short_hash = sha256[:12] if sha256 else uuid.uuid4().hex[:12]

    ext = ""
    if value and not value.startswith("data:"):
        source = urlparse(value).path if value.startswith("http") else value
        ext = Path(source).suffix

    if not ext:
        ext = _DEFAULT_EXTENSIONS.get(prefix, ".bin")

    return f"{prefix}_{short_hash}{ext}"
