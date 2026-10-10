# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import io
import os
from unittest.mock import AsyncMock, MagicMock, patch

import aiofiles
import pytest
import segno

from pyrit.converter import QRCodeConverter
from pyrit.memory import DataTypeSerializer


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


async def test_qr_code_converter_invalid_prompt() -> None:
    converter = QRCodeConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="", input_type="text")


async def test_qr_code_converter_convert_async(tmp_path) -> None:
    converter = QRCodeConverter()
    expected_filename = tmp_path / "sample_file.png"
    serializer = MagicMock(spec=DataTypeSerializer)
    serializer.get_data_filename_async = AsyncMock(return_value=expected_filename)
    with patch("pyrit.converter.qr_code_converter.data_serializer_factory", return_value=serializer) as mock_factory:
        qr = await converter.convert_async(prompt="Sample prompt", input_type="text")
        assert qr
        assert str(qr.output_text) == str(expected_filename)
        assert qr.output_type == "image_path"
        assert os.path.exists(qr.output_text)
        mock_factory.assert_called_once()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("concurrent", [False, True], ids=["sequential", "concurrent"])
@pytest.mark.parametrize(
    "prompts",
    [("first prompt", "second prompt"), ("same prompt", "same prompt")],
    ids=["different-prompts", "identical-prompts"],
)
async def test_qr_code_converter_writes_a_new_file_per_call_async(
    *, concurrent: bool, prompts: tuple[str, str]
) -> None:
    converter = QRCodeConverter()
    with patch("time.time", return_value=1_000_000.0):
        if concurrent:
            results = await asyncio.gather(*(converter.convert_async(prompt=prompt) for prompt in prompts))
        else:
            results = [await converter.convert_async(prompt=prompt) for prompt in prompts]

    assert results[0].output_text != results[1].output_text
    for result, prompt in zip(results, prompts, strict=True):
        assert result.output_type == "image_path"
        expected = io.BytesIO()
        segno.make_qr(prompt).save(expected, kind="png", scale=converter._scale, border=converter._border)
        async with aiofiles.open(result.output_text, "rb") as actual:
            assert await actual.read() == expected.getvalue()


def test_text_image_converter_input_supported():
    converter = QRCodeConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
