# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from PIL import Image

from pyrit.converter._image_url_downloader import _download_image_from_url_async
from pyrit.converter.base_image_to_image_converter import BaseImageToImageConverter
from pyrit.converter.image_compression_converter import ImageCompressionConverter


class _OverridingImageConverter(BaseImageToImageConverter):
    """Test converter that replaces URL reads through the protected hook."""

    def __init__(self, *, image_bytes: bytes) -> None:
        super().__init__()
        self.image_bytes = image_bytes
        self.urls_read: list[str] = []

    def _apply_transform(self, image: Image.Image) -> Image.Image:
        return image

    async def _read_image_from_url_async(self, url: str) -> bytes:
        self.urls_read.append(url)
        return self.image_bytes


def _mock_download_session() -> tuple[MagicMock, MagicMock, MagicMock]:
    client_session = MagicMock()
    session = MagicMock()
    response_context = MagicMock()
    response = MagicMock()

    client_session.__aenter__ = AsyncMock(return_value=session)
    response_context.__aenter__ = AsyncMock(return_value=response)
    session.get.return_value = response_context

    return client_session, session, response


async def test_download_image_from_url_async_returns_response_bytes():
    """The shared downloader returns raw response bytes after checking the status."""
    url = "https://example.com/image.png"
    client_session, session, response = _mock_download_session()
    response.read = AsyncMock(return_value=b"image bytes")

    with patch("pyrit.converter._image_url_downloader.aiohttp.ClientSession", return_value=client_session):
        result = await _download_image_from_url_async(url)

    assert result == b"image bytes"
    session.get.assert_called_once_with(url)
    response.raise_for_status.assert_called_once_with()
    response.read.assert_awaited_once_with()


async def test_download_image_from_url_async_wraps_non_success_response():
    """HTTP status failures retain the existing RuntimeError contract."""
    url = "https://example.com/missing.png"
    error = aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=404, message="Not Found")
    client_session, _, response = _mock_download_session()
    response.raise_for_status.side_effect = error

    with (
        patch("pyrit.converter._image_url_downloader.aiohttp.ClientSession", return_value=client_session),
        pytest.raises(RuntimeError) as exc_info,
    ):
        await _download_image_from_url_async(url)

    assert type(exc_info.value) is RuntimeError
    assert str(exc_info.value) == f"Failed to download content from URL {url}: {str(error)}"
    assert exc_info.value.__cause__ is error


async def test_download_image_from_url_async_wraps_connection_error():
    """Connection failures retain the existing RuntimeError contract."""
    url = "https://example.com/image.png"
    error = aiohttp.ClientConnectionError("connection failed")
    client_session, session, _ = _mock_download_session()
    session.get.return_value.__aenter__.side_effect = error

    with (
        patch("pyrit.converter._image_url_downloader.aiohttp.ClientSession", return_value=client_session),
        pytest.raises(RuntimeError) as exc_info,
    ):
        await _download_image_from_url_async(url)

    assert type(exc_info.value) is RuntimeError
    assert str(exc_info.value) == f"Failed to download content from URL {url}: {str(error)}"
    assert exc_info.value.__cause__ is error
    client_session.__aexit__.assert_awaited_once()


async def test_download_image_from_url_async_wraps_client_error_while_reading_body():
    """Client errors while reading the response body retain the RuntimeError contract."""
    url = "https://example.com/image.png"
    error = aiohttp.ClientPayloadError("body read failed")
    client_session, _, response = _mock_download_session()
    response.read = AsyncMock(side_effect=error)

    with (
        patch("pyrit.converter._image_url_downloader.aiohttp.ClientSession", return_value=client_session),
        pytest.raises(RuntimeError) as exc_info,
    ):
        await _download_image_from_url_async(url)

    assert type(exc_info.value) is RuntimeError
    assert str(exc_info.value) == f"Failed to download content from URL {url}: {str(error)}"
    assert exc_info.value.__cause__ is error


async def test_download_image_from_url_async_propagates_non_client_body_error():
    """Non-aiohttp body-read exceptions remain unwrapped."""
    url = "https://example.com/image.png"
    error = ValueError("body read failed")
    client_session, _, response = _mock_download_session()
    response.read = AsyncMock(side_effect=error)

    with (
        patch("pyrit.converter._image_url_downloader.aiohttp.ClientSession", return_value=client_session),
        pytest.raises(ValueError) as exc_info,
    ):
        await _download_image_from_url_async(url)

    assert exc_info.value is error


async def test_download_image_from_url_async_propagates_cancellation():
    """Cancellation is not caught by the aiohttp error wrapper."""
    client_session, _, response = _mock_download_session()
    response.read = AsyncMock(side_effect=asyncio.CancelledError())

    with (
        patch("pyrit.converter._image_url_downloader.aiohttp.ClientSession", return_value=client_session),
        pytest.raises(asyncio.CancelledError),
    ):
        await _download_image_from_url_async("https://example.com/image.png")


async def test_base_image_converter_url_reader_delegates_to_shared_downloader():
    """The base hook delegates URL reads to the shared internal downloader."""
    converter = _OverridingImageConverter(image_bytes=b"")

    with patch(
        "pyrit.converter.base_image_to_image_converter._download_image_from_url_async",
        new_callable=AsyncMock,
        return_value=b"image bytes",
    ) as mock_download:
        result = await BaseImageToImageConverter._read_image_from_url_async(converter, "https://example.com/image.png")

    assert result == b"image bytes"
    mock_download.assert_awaited_once_with("https://example.com/image.png")


async def test_image_compression_converter_url_reader_delegates_to_shared_downloader():
    """The compression hook delegates URL reads to the shared internal downloader."""
    converter = ImageCompressionConverter()

    with patch(
        "pyrit.converter.image_compression_converter._download_image_from_url_async",
        new_callable=AsyncMock,
        return_value=b"image bytes",
    ) as mock_download:
        result = await converter._read_image_from_url_async("https://example.com/image.png")

    assert result == b"image bytes"
    mock_download.assert_awaited_once_with("https://example.com/image.png")


async def test_base_image_converter_convert_async_honors_url_reader_override():
    """URL conversion continues to dispatch through subclass URL-reader overrides."""
    image = Image.new("RGB", (1, 1), color=(0, 0, 0))
    image_buffer = BytesIO()
    image.save(image_buffer, format="PNG")
    converter = _OverridingImageConverter(image_bytes=image_buffer.getvalue())
    serializer = MagicMock()
    serializer.value = "converted.png"
    serializer.save_b64_image_async = AsyncMock()

    with patch("pyrit.converter.base_image_to_image_converter.data_serializer_factory", return_value=serializer):
        result = await converter.convert_async(prompt="https://example.com/image.png", input_type="url")

    assert result.output_text == "converted.png"
    assert converter.urls_read == ["https://example.com/image.png"]
    serializer.save_b64_image_async.assert_awaited_once()
