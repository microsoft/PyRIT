# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared image-fetch-and-cache helper for multimodal seed-dataset loaders.

Multiple loaders under ``pyrit.datasets.seed_datasets.remote`` need to
download an image from a URL (or write bytes already in hand), persist it
under the ``seed-prompt-entries`` cache, and return the local path while
skipping the network call on a cache hit. This module centralizes that
logic so individual loaders only need to construct the appropriate filename.
"""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from pyrit.common.net_utility import make_request_and_raise_if_error_async
from pyrit.models import data_serializer_factory

logger = logging.getLogger(__name__)


async def fetch_and_cache_image_async(
    *,
    filename: str,
    image_url: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    log_prefix: str = "image-cache",
    request_headers: Optional[Mapping[str, str]] = None,
    request_timeout: Optional[float] = None,
    follow_redirects: bool = False,
) -> str:
    """
    Fetch (or accept) image bytes and cache them under ``seed-prompt-entries``.

    The cached path is constructed deterministically from the configured
    ``results_path`` plus the serializer's ``data_sub_directory`` plus
    ``filename``, normalized through ``pathlib.Path`` so the same on-disk
    location is produced on Windows and POSIX. If a file already exists at
    that path, the path is returned immediately without performing the network
    fetch or rewriting bytes.

    Exactly one of ``image_url`` or ``image_bytes`` must be provided. When
    ``image_bytes`` is supplied, the network is never contacted regardless of
    whether ``image_url`` is also provided.

    Args:
        filename (str): On-disk filename for the cached image, including
            extension (e.g. ``"harmbench_<id>.png"``). The caller controls this
            so per-loader naming and existing cache files stay intact.
        image_url (str | None): URL to fetch the image from. Required when
            ``image_bytes`` is not provided.
        image_bytes (bytes | None): Raw image bytes (e.g. extracted from a PIL
            image). When provided, no network request is made.
        log_prefix (str): Short tag prepended to warning log messages
            (e.g. ``"HarmBench-Multimodal"``) so existing log output stays
            recognizable per-loader.
        request_headers (Mapping[str, str] | None): Optional HTTP headers to
            send with the request (only used when fetching from ``image_url``).
        request_timeout (float | None): Optional request timeout in seconds.
        follow_redirects (bool): Whether the HTTP client should follow
            redirects. Defaults to ``False``.

    Returns:
        str: Local path to the cached image.

    Raises:
        ValueError: If neither ``image_url`` nor ``image_bytes`` is provided.
        RuntimeError: If the serializer's underlying memory is not properly
            configured (``results_path`` or ``results_storage_io`` missing).
        Exception: Any error raised by the underlying HTTP fetch or by
            ``serializer.save_data`` is propagated so callers can catch and
            skip individual rows.
    """
    if image_bytes is None and not image_url:
        raise ValueError("fetch_and_cache_image_async requires either image_url or image_bytes")

    extension = Path(filename).suffix.lstrip(".") or None

    serializer = data_serializer_factory(
        category="seed-prompt-entries",
        data_type="image_path",
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
        if await results_storage_io.path_exists(serializer.value):
            return serializer.value
    except Exception as e:
        logger.warning(f"[{log_prefix}] Failed to check if cached image {filename} exists: {e}")

    if image_bytes is None:
        # image_url is guaranteed non-empty by the validation above when image_bytes is None.
        assert image_url is not None
        httpx_kwargs: dict[str, Any] = {"follow_redirects": follow_redirects}
        if request_timeout is not None:
            httpx_kwargs["timeout"] = request_timeout

        headers_dict = dict(request_headers) if request_headers is not None else None
        response = await make_request_and_raise_if_error_async(
            endpoint_uri=image_url,
            method="GET",
            headers=headers_dict,
            **httpx_kwargs,
        )
        image_bytes = response.content

    await serializer.save_data(data=image_bytes, output_filename=Path(filename).stem)

    return str(serializer.value)
