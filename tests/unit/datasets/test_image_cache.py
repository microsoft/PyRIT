# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote._image_cache import (
    fetch_and_cache_image_async,
)


def _make_mock_serializer(*, exists: bool = False) -> MagicMock:
    """Build a MagicMock serializer with memory configured."""
    mock_serializer = MagicMock()
    mock_memory = MagicMock()
    mock_memory.results_path = "/results"
    mock_storage_io = AsyncMock()
    mock_storage_io.path_exists = AsyncMock(return_value=exists)
    mock_memory.results_storage_io = mock_storage_io
    mock_serializer._memory = mock_memory
    mock_serializer.data_sub_directory = "/seed-prompt-entries/images"
    mock_serializer.save_data = AsyncMock()
    return mock_serializer


async def test_returns_cached_path_when_file_exists_and_skips_network():
    mock_serializer = _make_mock_serializer(exists=True)

    with (
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.data_serializer_factory",
            return_value=mock_serializer,
        ),
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.make_request_and_raise_if_error_async",
            new=AsyncMock(),
        ) as mock_request,
    ):
        result = await fetch_and_cache_image_async(
            filename="test_image.png",
            image_url="https://example.com/image.png",
            log_prefix="TestLoader",
        )

    expected_path = str(Path("/results") / "seed-prompt-entries" / "images" / "test_image.png")
    assert result == expected_path
    assert mock_serializer.value == expected_path
    mock_request.assert_not_called()
    mock_serializer.save_data.assert_not_called()


async def test_downloads_when_cache_miss_and_writes_bytes():
    mock_serializer = _make_mock_serializer(exists=False)

    mock_response = MagicMock()
    mock_response.content = b"fake-image-bytes"

    with (
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.data_serializer_factory",
            return_value=mock_serializer,
        ),
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.make_request_and_raise_if_error_async",
            new=AsyncMock(return_value=mock_response),
        ) as mock_request,
    ):
        await fetch_and_cache_image_async(
            filename="test_image.png",
            image_url="https://example.com/image.png",
            log_prefix="TestLoader",
        )

    mock_request.assert_called_once()
    assert mock_request.call_args.kwargs["endpoint_uri"] == "https://example.com/image.png"
    assert mock_request.call_args.kwargs["method"] == "GET"

    mock_serializer.save_data.assert_called_once()
    save_kwargs = mock_serializer.save_data.call_args.kwargs
    assert save_kwargs["data"] == b"fake-image-bytes"
    assert save_kwargs["output_filename"] == "test_image"


async def test_image_bytes_path_skips_network_call():
    mock_serializer = _make_mock_serializer(exists=False)

    with (
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.data_serializer_factory",
            return_value=mock_serializer,
        ),
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.make_request_and_raise_if_error_async",
            new=AsyncMock(),
        ) as mock_request,
    ):
        await fetch_and_cache_image_async(
            filename="bytes_image.png",
            image_bytes=b"raw-pil-bytes",
            log_prefix="TestLoader",
        )

    mock_request.assert_not_called()
    mock_serializer.save_data.assert_called_once()
    assert mock_serializer.save_data.call_args.kwargs["data"] == b"raw-pil-bytes"
    assert mock_serializer.save_data.call_args.kwargs["output_filename"] == "bytes_image"


async def test_raises_value_error_when_neither_url_nor_bytes_provided():
    with pytest.raises(ValueError, match="either image_url or image_bytes"):
        await fetch_and_cache_image_async(filename="test.png")


async def test_raises_runtime_error_when_memory_not_configured():
    mock_serializer = MagicMock()
    mock_memory = MagicMock()
    mock_memory.results_path = None
    mock_memory.results_storage_io = None
    mock_serializer._memory = mock_memory

    with patch(
        "pyrit.datasets.seed_datasets.remote._image_cache.data_serializer_factory",
        return_value=mock_serializer,
    ):
        with pytest.raises(RuntimeError, match="Serializer memory is not properly configured"):
            await fetch_and_cache_image_async(
                filename="test.png",
                image_url="https://example.com/img.png",
            )


async def test_propagates_http_failures():
    mock_serializer = _make_mock_serializer(exists=False)

    with (
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.data_serializer_factory",
            return_value=mock_serializer,
        ),
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.make_request_and_raise_if_error_async",
            new=AsyncMock(side_effect=Exception("download failed")),
        ),
    ):
        with pytest.raises(Exception, match="download failed"):
            await fetch_and_cache_image_async(
                filename="test.png",
                image_url="https://example.com/img.png",
            )

    mock_serializer.save_data.assert_not_called()


async def test_passes_custom_headers_timeout_and_redirects_to_http_client():
    mock_serializer = _make_mock_serializer(exists=False)
    mock_response = MagicMock()
    mock_response.content = b"bytes"

    custom_headers = {"User-Agent": "test-agent", "Accept": "image/*"}

    with (
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.data_serializer_factory",
            return_value=mock_serializer,
        ),
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.make_request_and_raise_if_error_async",
            new=AsyncMock(return_value=mock_response),
        ) as mock_request,
    ):
        await fetch_and_cache_image_async(
            filename="custom.png",
            image_url="https://example.com/img.png",
            request_headers=custom_headers,
            request_timeout=5.0,
            follow_redirects=True,
        )

    kwargs = mock_request.call_args.kwargs
    assert kwargs["headers"] == custom_headers
    assert kwargs["timeout"] == 5.0
    assert kwargs["follow_redirects"] is True


async def test_path_exists_failure_is_logged_and_treated_as_cache_miss():
    mock_serializer = _make_mock_serializer(exists=False)
    mock_serializer._memory.results_storage_io.path_exists = AsyncMock(side_effect=Exception("storage IO unavailable"))

    mock_response = MagicMock()
    mock_response.content = b"bytes"

    with (
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.data_serializer_factory",
            return_value=mock_serializer,
        ),
        patch(
            "pyrit.datasets.seed_datasets.remote._image_cache.make_request_and_raise_if_error_async",
            new=AsyncMock(return_value=mock_response),
        ) as mock_request,
    ):
        await fetch_and_cache_image_async(
            filename="failing_cache.png",
            image_url="https://example.com/img.png",
        )

    # Treated as cache miss: fetch happens and save runs.
    mock_request.assert_called_once()
    mock_serializer.save_data.assert_called_once()
