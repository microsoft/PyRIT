# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.prompt_converter import QRCodeConverter


def test_qr_code_converter_initialization():
    converter = QRCodeConverter(
        scale=5,
        border=4,
        dark_color=(0, 0, 0),
        light_color=(255, 255, 255),
        data_dark_color=(0, 0, 0),
        data_light_color=(255, 255, 255),
        finder_dark_color=(0, 0, 0),
        finder_light_color=(255, 255, 255),
        border_color=(255, 255, 255),
    )
    assert converter._scale == 5
    assert converter._border == 4
    assert converter._dark_color == (0, 0, 0)
    assert converter._light_color == (255, 255, 255)
    assert converter._data_dark_color == (0, 0, 0)
    assert converter._data_light_color == (255, 255, 255)
    assert converter._finder_dark_color == (0, 0, 0)
    assert converter._finder_light_color == (255, 255, 255)
    assert converter._border_color == (255, 255, 255)


def test_qr_code_converter_color_initialization():
    converter = QRCodeConverter(dark_color=(2, 0, 2), light_color=(100, 150, 100))
    assert converter._dark_color == (2, 0, 2)
    assert converter._light_color == (100, 150, 100)
    assert converter._data_dark_color == converter._dark_color
    assert converter._data_light_color == converter._light_color
    assert converter._finder_dark_color == converter._dark_color
    assert converter._finder_light_color == converter._light_color
    assert converter._border_color == converter._light_color


@pytest.mark.asyncio
async def test_qr_code_converter_invalid_prompt() -> None:
    converter = QRCodeConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="", input_type="text")


@pytest.mark.asyncio
async def test_qr_code_converter_convert_async() -> None:
    converter = QRCodeConverter()
    with patch.object(converter._img_serializer, "get_data_filename") as mock_get_data_filename:
        expected_filename = Path("sample_file.png").resolve()
        mock_get_data_filename.return_value = expected_filename
        qr = await converter.convert_async(prompt="Sample prompt", input_type="text")
        assert qr
        assert str(qr.output_text) == str(expected_filename)
        assert qr.output_type == "image_path"
        assert os.path.exists(qr.output_text)
        os.remove(qr.output_text)


def test_text_image_converter_input_supported():
    converter = QRCodeConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
