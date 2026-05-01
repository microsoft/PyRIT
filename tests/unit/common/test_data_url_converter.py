# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from tempfile import NamedTemporaryFile
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.common.data_url_converter import (
    AZURE_OPENAI_GPT4O_SUPPORTED_IMAGE_FORMATS,
    convert_local_image_to_data_url,
)


def test_supported_image_formats_contains_common_types():
    assert ".jpg" in AZURE_OPENAI_GPT4O_SUPPORTED_IMAGE_FORMATS
    assert ".png" in AZURE_OPENAI_GPT4O_SUPPORTED_IMAGE_FORMATS
    assert ".gif" in AZURE_OPENAI_GPT4O_SUPPORTED_IMAGE_FORMATS


async def test_convert_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        await convert_local_image_to_data_url("nonexistent_image.jpg")


async def test_convert_raises_for_unsupported_format():
    with NamedTemporaryFile(suffix=".svg", delete=False) as f:
        tmp = f.name
    try:
        with pytest.raises(ValueError, match="Unsupported image format"):
            await convert_local_image_to_data_url(tmp)
    finally:
        os.remove(tmp)


async def test_convert_returns_data_url():
    with NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    try:
        mock_serializer = AsyncMock()
        mock_serializer.read_data_base64 = AsyncMock(return_value="AAAA")

        with patch("pyrit.common.data_url_converter.data_serializer_factory", return_value=mock_serializer):
            result = await convert_local_image_to_data_url(tmp)

        assert result.startswith("data:image/png;base64,")
        assert result.endswith("AAAA")
    finally:
        os.remove(tmp)
