# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared audio-bytes-cache helper for multimodal seed-dataset loaders.

Mirrors ``_image_cache`` but for audio: persists raw audio bytes already in hand
(e.g. extracted from a HuggingFace ``Audio(decode=False)`` column) under the
``seed-prompt-entries`` cache and returns the local path, skipping the write on a
cache hit.
"""

import logging
from pathlib import Path

from pyrit.memory import data_serializer_factory

logger = logging.getLogger(__name__)


async def cache_audio_bytes_async(
    *,
    filename: str,
    audio_bytes: bytes,
    log_prefix: str = "audio-cache",
) -> str:
    """
    Persist audio bytes under ``seed-prompt-entries`` and return the local path.

    The cached path is constructed deterministically from the serializer's
    ``results_path`` plus its ``data_sub_directory`` plus ``filename``. If a file
    already exists at that path, the path is returned without rewriting the bytes.

    Args:
        filename: On-disk filename for the cached audio, including extension
            (e.g. ``"garak_audio_achilles_heel_<stem>.wav"``).
        audio_bytes: Raw audio file bytes to persist.
        log_prefix: Short tag prepended to warning log messages.

    Returns:
        str: Local path to the cached audio file.

    Raises:
        RuntimeError: If the serializer's underlying memory is not configured.
    """
    extension = Path(filename).suffix.lstrip(".") or None

    serializer = data_serializer_factory(
        category="seed-prompt-entries",
        data_type="audio_path",
        extension=extension,
    )

    results_path = serializer._memory.results_path if serializer._memory is not None else None
    results_storage_io = serializer._memory.results_storage_io if serializer._memory is not None else None
    if not results_path or results_storage_io is None:
        raise RuntimeError(
            f"[{log_prefix}] Serializer memory is not properly configured: "
            "results_path and results_storage_io must be set."
        )

    sub_directory = serializer.data_sub_directory.lstrip("/\\")
    serializer.value = str(Path(results_path) / sub_directory / filename)

    try:
        if await results_storage_io.path_exists_async(serializer.value):
            return serializer.value
    except Exception as e:
        logger.warning(f"[{log_prefix}] Failed to check if cached audio {filename} exists: {e}")

    await serializer.save_data_async(data=audio_bytes, output_filename=Path(filename).stem)

    return str(serializer.value)
