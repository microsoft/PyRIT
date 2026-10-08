# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the bounded media URL download."""

import logging
from collections.abc import Callable, Iterator

import httpx
import pytest

from pyrit.backend.services import media_url_import
from pyrit.backend.services.media_url_import import (
    MAX_MEDIA_URL_REDIRECTS,
    MediaDownload,
    download_media_url_async,
    media_content_type,
    media_extension,
    redact_url,
    set_media_url_import_enabled,
)

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], list[httpx.Request]]:
    """Route media downloads through a mock transport and record the requests it receives."""

    def install(handler: Handler) -> list[httpx.Request]:
        requests: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        def create_client() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(record), headers={"Accept-Encoding": "identity"})

        monkeypatch.setattr(media_url_import, "_create_client", create_client)
        return requests

    return install


@pytest.fixture(autouse=True)
def url_import_enabled() -> Iterator[None]:
    yield
    set_media_url_import_enabled(enabled=True)


async def test_download_returns_content_and_type(transport: Callable[[Handler], list[httpx.Request]]) -> None:
    requests = transport(lambda request: httpx.Response(200, content=b"PNG", headers={"content-type": "image/png"}))

    download = await download_media_url_async(url="https://example.test/cat.png?sig=secret")

    assert download == MediaDownload(
        content=b"PNG", content_type="image/png", final_url="https://example.test/cat.png?sig=secret"
    )
    assert len(requests) == 1
    assert requests[0].headers["accept-encoding"] == "identity"
    assert "authorization" not in requests[0].headers


async def test_declared_length_over_limit_is_rejected(transport: Callable[[Handler], list[httpx.Request]]) -> None:
    transport(
        lambda request: httpx.Response(
            200, content=b"x", headers={"content-length": str(media_url_import.MAX_MEDIA_URL_BYTES + 1)}
        )
    )

    with pytest.raises(ValueError, match="larger than 100 MiB"):
        await download_media_url_async(url="https://example.test/big.bin")


async def test_streamed_body_over_limit_is_rejected(
    transport: Callable[[Handler], list[httpx.Request]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_url_import, "MAX_MEDIA_URL_BYTES", 4)

    async def body():
        for chunk in (b"abc", b"def"):
            yield chunk

    transport(lambda request: httpx.Response(200, content=body()))

    with pytest.raises(ValueError, match="larger than"):
        await download_media_url_async(url="https://example.test/stream.bin")


async def test_too_many_redirects_are_rejected(transport: Callable[[Handler], list[httpx.Request]]) -> None:
    requests = transport(
        lambda request: httpx.Response(302, headers={"location": f"https://example.test/{len(request.url.path)}x"})
    )

    with pytest.raises(ValueError, match=f"more than {MAX_MEDIA_URL_REDIRECTS} times"):
        await download_media_url_async(url="https://example.test/start")
    assert len(requests) == MAX_MEDIA_URL_REDIRECTS + 1


@pytest.mark.parametrize("location", ["file:///etc/hostname", "https://user:secret@example.test/next.png"])
async def test_redirect_to_non_plain_http_url_is_rejected(
    transport: Callable[[Handler], list[httpx.Request]], location: str
) -> None:
    requests = transport(lambda request: httpx.Response(302, headers={"location": location}))

    with pytest.raises(ValueError, match="redirected to a URL that is not a plain http or https URL") as error:
        await download_media_url_async(url="https://example.test/start")
    assert len(requests) == 1
    assert "secret" not in str(error.value)


async def test_redirect_body_is_not_read(
    transport: Callable[[Handler], list[httpx.Request]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_url_import, "MAX_MEDIA_URL_BYTES", 4)
    read_redirect_body = False

    async def redirect_body():
        nonlocal read_redirect_body
        read_redirect_body = True
        yield b"x" * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/done.png"}, content=redirect_body())
        return httpx.Response(200, content=b"ok", headers={"content-type": "image/png"})

    transport(handler)

    download = await download_media_url_async(url="https://example.test/start")

    assert download.content == b"ok"
    assert download.final_url == "https://example.test/done.png"
    assert not read_redirect_body


async def test_error_status_is_rejected_without_query_string(
    transport: Callable[[Handler], list[httpx.Request]], caplog: pytest.LogCaptureFixture
) -> None:
    transport(lambda request: httpx.Response(404))

    with (
        caplog.at_level(logging.WARNING, logger=media_url_import.__name__),
        pytest.raises(ValueError, match="returned HTTP 404") as error,
    ):
        await download_media_url_async(url="https://user.example.test/cat.png?sv=1&sig=secret#frag")
    assert "secret" not in str(error.value)
    assert "https://user.example.test/cat.png" in str(error.value)
    assert "HTTP 404 (HTTPStatusError)" in caplog.text
    assert "secret" not in caplog.text


_SIGNED_DETAIL = "failed for https://example.test/slow.png?sig=secret"


@pytest.mark.parametrize(
    ("make_error", "reason", "cause"),
    [
        (
            lambda request: httpx.ConnectTimeout(_SIGNED_DETAIL, request=request),
            "connecting timed out after 10 seconds",
            "ConnectTimeout",
        ),
        (
            lambda request: httpx.ReadTimeout(_SIGNED_DETAIL, request=request),
            "no data arrived for 30 seconds",
            "ReadTimeout",
        ),
        (
            lambda request: httpx.WriteTimeout(_SIGNED_DETAIL, request=request),
            "sending the request timed out after 30 seconds",
            "WriteTimeout",
        ),
        (
            lambda request: httpx.PoolTimeout(_SIGNED_DETAIL, request=request),
            "no connection was free within 30 seconds",
            "PoolTimeout",
        ),
        (lambda request: TimeoutError(_SIGNED_DETAIL), "the download took longer than 60 seconds", "TimeoutError"),
        (lambda request: httpx.ConnectError(_SIGNED_DETAIL, request=request), "the connection failed", "ConnectError"),
        (
            lambda request: httpx.RemoteProtocolError(_SIGNED_DETAIL, request=request),
            "the request failed (RemoteProtocolError)",
            "RemoteProtocolError",
        ),
    ],
)
async def test_network_failures_name_the_reason_and_limit(
    transport: Callable[[Handler], list[httpx.Request]],
    caplog: pytest.LogCaptureFixture,
    make_error: Callable[[httpx.Request], BaseException],
    reason: str,
    cause: str,
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise make_error(request)

    transport(fail)

    with (
        caplog.at_level(logging.WARNING, logger=media_url_import.__name__),
        pytest.raises(ValueError) as error,
    ):
        await download_media_url_async(url="https://example.test/slow.png?sig=secret")

    assert str(error.value) == f"Media URL https://example.test/slow.png could not be downloaded: {reason}."
    assert error.value.__cause__ is not None
    assert f"https://example.test/slow.png could not be downloaded: {reason} ({cause}" in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://example.test/cat.png", "http or https"),
        ("https:///cat.png", "http or https"),
        ("https://user:pass@example.test/cat.png", "credentials"),
    ],
)
async def test_invalid_urls_are_rejected_before_download(
    transport: Callable[[Handler], list[httpx.Request]], url: str, message: str
) -> None:
    requests = transport(lambda request: httpx.Response(200))

    with pytest.raises(ValueError, match=message):
        await download_media_url_async(url=url)
    assert requests == []


async def test_disabled_import_rejects_urls(transport: Callable[[Handler], list[httpx.Request]]) -> None:
    requests = transport(lambda request: httpx.Response(200))
    set_media_url_import_enabled(enabled=False)

    with pytest.raises(ValueError, match="turned off"):
        await download_media_url_async(url="https://example.test/cat.png")
    assert requests == []


def test_redact_url_drops_credentials_query_and_fragment() -> None:
    assert redact_url("https://user:pw@example.test:8443/a/b.png?sig=1#x") == "https://example.test:8443/a/b.png"


@pytest.mark.parametrize(
    ("content_type", "final_url", "expected_type", "expected_extension"),
    [
        ("image/png; charset=binary", "https://example.test/a", "image/png", ".png"),
        ("application/octet-stream", "https://example.test/a/photo.JPG", "image/jpeg", ".jpg"),
        (None, "https://example.test/a/clip.mp4?sig=1", "video/mp4", ".mp4"),
        (None, "https://example.test/a/blob", None, ".bin"),
        ("application/octet-stream", "https://example.test/a/file.weird123", None, ".weird123"),
        (None, "https://example.test/a/file.toolongsuffix1", None, ".bin"),
    ],
)
def test_media_type_and_extension(
    content_type: str | None, final_url: str, expected_type: str | None, expected_extension: str
) -> None:
    download = MediaDownload(content=b"", content_type=content_type, final_url=final_url)

    assert media_content_type(download) == expected_type
    assert media_extension(download, default=".bin") == expected_extension
