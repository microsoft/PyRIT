# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Presentation-layer formatter for ``ConversationStats.last_message_preview``.

Lives in the backend mapper package because the formatting it produces
(``[Image: <basename>]`` etc.) is purely a display concern for the GUI API
responses — the memory layer stays data-agnostic and just stores the raw
value + data type.

The motivating bug: ``converted_value`` for media-path data types
(``image_path`` / ``audio_path`` / ``video_path`` / ``binary_path``) is a
filesystem path or blob URL. Rendering it raw in the Attack History preview
leaks the absolute on-disk location of memory artifacts
(e.g. ``C:\\Users\\<name>\\git\\PyRIT\\dbdata\\...\\1780.mp3``).
"""

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from pyrit.models import ConversationStats
from pyrit.models.literals import MEDIA_PATH_DATA_TYPES

# Friendly label per media-path data type. Kept here next to the formatter
# so adding a new media type only requires updating one place.
_MEDIA_LABEL: dict[str, str] = {
    "image_path": "Image",
    "audio_path": "Audio",
    "video_path": "Video",
    "binary_path": "File",
}


def _derive_basename(value: str) -> Optional[str]:
    """
    Return a display-safe basename for *value*.

    Args:
        value: A filesystem path, URL, or other reference.

    Returns:
        The basename (filename portion) of *value*, or ``None`` if one can't
        be derived (e.g. data URI, empty value).
    """
    if not value or value.startswith("data:"):
        return None
    if value.startswith(("http://", "https://")):
        # Strip query string (e.g. SAS tokens) before taking the basename.
        parsed = urlparse(value)
        name = Path(parsed.path).name
        return name or None
    # Local path — Path handles both POSIX and Windows separators.
    return Path(value).name or None


def format_last_message_preview(
    *,
    value: Optional[str],
    data_type: Optional[str],
    max_len: int = ConversationStats.PREVIEW_MAX_LEN,
) -> Optional[str]:
    """
    Build a display string for ``ConversationStats.last_message_preview``.

    Media-path data types are rendered as ``[Image: <basename>]`` (and
    variants) so the absolute filesystem path of memory artifacts is never
    exposed through API responses or UI previews. Text-like data types pass
    through with truncation and an ellipsis suffix when they exceed
    *max_len*.

    Args:
        value: Raw ``converted_value`` for the last piece (or ``None``).
        data_type: ``converted_value_data_type`` for that piece. ``None``
            falls back to the text path.
        max_len: Maximum length for text previews before truncation.

    Returns:
        The formatted preview string, or ``None`` when there is nothing
        meaningful to show.
    """
    if data_type in MEDIA_PATH_DATA_TYPES:
        # MEDIA_PATH_DATA_TYPES guarantees ``data_type`` is a key in
        # ``_MEDIA_LABEL`` — both are derived from the same source list.
        label = _MEDIA_LABEL[data_type]
        basename = _derive_basename(value or "")
        return f"[{label}: {basename}]" if basename else f"[{label}]"

    if not value:
        return None

    if len(value) > max_len:
        return value[:max_len] + "..."
    return value
