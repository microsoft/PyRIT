# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from tempfile import NamedTemporaryFile
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from pyrit.memory.storage.data_url_converter import (
    OPENAI_VISION_SUPPORTED_IMAGE_FORMATS,
    convert_local_image_to_data_url_async,
)


def test_supported_image_formats_match_openai_vision_formats():
    assert set(OPENAI_VISION_SUPPORTED_IMAGE_FORMATS) == {".jpg", ".jpeg", ".png", ".webp", ".gif"}


async def test_convert_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        await convert_local_image_to_data_url_async("nonexistent_image.jpg")


@pytest.mark.parametrize("suffix", [".bmp", ".tiff", ".tif", ".svg"])
async def test_convert_raises_for_unsupported_format(suffix: str):
    with NamedTemporaryFile(suffix=suffix, delete=False) as f:
        tmp = f.name
    try:
        with pytest.raises(ValueError, match="Unsupported image format"):
            await convert_local_image_to_data_url_async(tmp)
    finally:
        os.remove(tmp)


@pytest.mark.parametrize(
    ("suffix", "mime_type"),
    [
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".png", "image/png"),
        (".webp", "image/webp"),
    ],
)
async def test_convert_supported_format_returns_data_url(suffix: str, mime_type: str):
    with NamedTemporaryFile(suffix=suffix, delete=False) as f:
        tmp = f.name
    try:
        mock_serializer = AsyncMock()
        mock_serializer.read_data_base64_async = AsyncMock(return_value="AAAA")

        with patch("pyrit.memory.storage.data_url_converter.data_serializer_factory", return_value=mock_serializer):
            result = await convert_local_image_to_data_url_async(tmp)

        assert result == f"data:{mime_type};base64,AAAA"
    finally:
        os.remove(tmp)


async def test_convert_animated_gif_returns_data_url():
    with NamedTemporaryFile(suffix=".gif", delete=False) as f:
        tmp = f.name

    frames = [Image.new("RGB", (1, 1), color) for color in ("red", "blue")]
    frames[0].save(tmp, save_all=True, append_images=frames[1:], duration=100, loop=0)

    try:
        mock_serializer = AsyncMock()
        mock_serializer.read_data_base64_async = AsyncMock(return_value="AAAA")

        with patch("pyrit.memory.storage.data_url_converter.data_serializer_factory", return_value=mock_serializer):
            result = await convert_local_image_to_data_url_async(tmp)

        assert result == "data:image/gif;base64,AAAA"
    finally:
        os.remove(tmp)
