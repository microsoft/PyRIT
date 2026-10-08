# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded download of caller-supplied media URLs into managed storage."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

import httpx

from pyrit.common.net_utility import get_httpx_client

logger = logging.getLogger(__name__)

MAX_MEDIA_URL_BYTES = 100 * 1024 * 1024
MAX_MEDIA_URL_REDIRECTS = 3
_CONNECT_TIMEOUT_SECONDS = 10.0
_READ_TIMEOUT_SECONDS = 30.0
_TIMEOUT = httpx.Timeout(_READ_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)
_DEADLINE_SECONDS = 60.0
_GENERIC_CONTENT_TYPES = frozenset({"application/octet-stream", "binary/octet-stream"})
_URL_SUFFIX_PATTERN = re.compile(r"^\.[A-Za-z0-9]{1,10}$")

_url_import_enabled = True


@dataclass(frozen=True)
class MediaDownload:
    """Bytes downloaded from a media URL and what the server reported about them."""

    content: bytes
    content_type: str | None
    final_url: str


def set_media_url_import_enabled(*, enabled: bool) -> None:
    """Turn the download of caller-supplied media URLs on or off."""
    global _url_import_enabled
    _url_import_enabled = enabled


def redact_url(url: str) -> str:
    """
    Return a URL without credentials, query string, or fragment, for use in error messages.

    Returns:
        str: The scheme, host, port, and path of the URL.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}{parsed.path}"


def media_content_type(download: MediaDownload) -> str | None:
    """
    Return the reported media type, or the type implied by the URL suffix when it is missing or generic.

    Returns:
        str | None: The lowercase media type without parameters, or None when unknown.
    """
    content_type = (download.content_type or "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in _GENERIC_CONTENT_TYPES:
        return content_type
    guessed, _ = mimetypes.guess_type(urlparse(download.final_url).path, strict=False)
    return guessed


def media_extension(download: MediaDownload, *, default: str) -> str:
    """
    Choose the file extension for downloaded media.

    Returns:
        str: The extension implied by the media type, else a short suffix from the URL path, else ``default``.
    """
    content_type = media_content_type(download)
    extension = mimetypes.guess_extension(content_type, strict=False) if content_type else None
    if extension:
        return extension
    suffix = PurePosixPath(urlparse(download.final_url).path).suffix
    return suffix.lower() if _URL_SUFFIX_PATTERN.match(suffix) else default


def _create_client() -> httpx.AsyncClient:
    return get_httpx_client(use_async=True, timeout=_TIMEOUT, headers={"Accept-Encoding": "identity"})


def _is_plain_http_url(url: httpx.URL) -> bool:
    """
    Return whether a URL is http(s), names a host, and carries no user information.

    Returns:
        bool: True for a URL the download may request.
    """
    return url.scheme in ("http", "https") and bool(url.host) and not url.userinfo


async def _read_download_async(*, response: httpx.Response, shown: str) -> MediaDownload:
    """
    Read a final response body within the size limit.

    Returns:
        MediaDownload: The downloaded bytes and the reported content type.

    Raises:
        ValueError: If the body is larger than the limit.
        httpx.HTTPStatusError: If the response is not successful.
    """
    response.raise_for_status()
    too_large = f"Media URL {shown} is larger than {MAX_MEDIA_URL_BYTES // (1024 * 1024)} MiB."
    declared_length = response.headers.get("content-length", "")
    if declared_length.isdigit() and int(declared_length) > MAX_MEDIA_URL_BYTES:
        raise ValueError(too_large)
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_MEDIA_URL_BYTES:
            raise ValueError(too_large)
        chunks.append(chunk)
    return MediaDownload(
        content=b"".join(chunks),
        content_type=response.headers.get("content-type"),
        final_url=str(response.url),
    )


async def download_media_url_async(*, url: str) -> MediaDownload:
    """
    Download a media URL once, with a time limit, a size limit, and a redirect limit.

    No credentials or headers from the API request are forwarded.

    Returns:
        MediaDownload: The downloaded bytes and the reported content type.

    Raises:
        ValueError: If URL import is turned off, the URL is not a plain http(s) URL, or the download fails or
            exceeds a limit.
    """
    if not _url_import_enabled:
        raise ValueError("Media URL import is turned off on this server; upload the file content instead.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Media URLs must be http or https URLs.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Media URLs must not include credentials.")

    shown = redact_url(url)
    try:
        async with asyncio.timeout(_DEADLINE_SECONDS), _create_client() as client:
            request = client.build_request("GET", url)
            # Redirects are followed here rather than by httpx, so each hop is checked and a
            # redirect body is closed unread instead of being buffered outside the size limit.
            for _ in range(MAX_MEDIA_URL_REDIRECTS + 1):
                response = await client.send(request, stream=True, follow_redirects=False)
                try:
                    if response.next_request is None:
                        return await _read_download_async(response=response, shown=shown)
                    request = response.next_request
                finally:
                    await response.aclose()
                if not _is_plain_http_url(request.url):
                    raise ValueError(f"Media URL {shown} redirected to a URL that is not a plain http or https URL.")
            raise ValueError(f"Media URL {shown} redirected more than {MAX_MEDIA_URL_REDIRECTS} times.")
    except httpx.HTTPStatusError as exc:
        _log_download_failure(shown=shown, reason=f"HTTP {exc.response.status_code}", exc=exc)
        raise ValueError(f"Media URL {shown} returned HTTP {exc.response.status_code}.") from exc
    except (httpx.HTTPError, TimeoutError) as exc:
        reason = _failure_reason(exc)
        _log_download_failure(shown=shown, reason=reason, exc=exc)
        raise ValueError(f"Media URL {shown} could not be downloaded: {reason}.") from exc


def _failure_reason(exc: BaseException) -> str:
    """
    Describe why a download failed, with the limit that was hit, without quoting the exception.

    httpx exception messages can include the full URL and its query string, so they are not repeated.

    Returns:
        str: A short reason such as ``connecting timed out after 10 seconds``.
    """
    if isinstance(exc, httpx.ConnectTimeout):
        return f"connecting timed out after {_CONNECT_TIMEOUT_SECONDS:g} seconds"
    if isinstance(exc, httpx.ReadTimeout):
        return f"no data arrived for {_READ_TIMEOUT_SECONDS:g} seconds"
    if isinstance(exc, httpx.WriteTimeout):
        return f"sending the request timed out after {_READ_TIMEOUT_SECONDS:g} seconds"
    if isinstance(exc, httpx.PoolTimeout):
        return f"no connection was free within {_READ_TIMEOUT_SECONDS:g} seconds"
    if isinstance(exc, TimeoutError):
        return f"the download took longer than {_DEADLINE_SECONDS:g} seconds"
    if isinstance(exc, httpx.ConnectError):
        return "the connection failed"
    return f"the request failed ({type(exc).__name__})"


def _log_download_failure(*, shown: str, reason: str, exc: BaseException) -> None:
    """Log a failed download with the redacted URL and the exception classes in its cause chain."""
    causes: list[str] = []
    cause: BaseException | None = exc
    while cause is not None and len(causes) < 4:
        causes.append(type(cause).__name__)
        cause = cause.__cause__ or cause.__context__
    logger.warning("Media URL %s could not be downloaded: %s (%s)", shown, reason, " <- ".join(causes))
