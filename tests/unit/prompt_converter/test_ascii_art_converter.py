# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

pytest.importorskip("art")

from pyrit.prompt_converter import AsciiArtConverter, ConverterResult


@pytest.mark.asyncio
async def test_ascii_art_converter_basic():
    converter = AsciiArtConverter(font="block")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) > 0
    assert "\n" in result.output_text


@pytest.mark.asyncio
async def test_ascii_art_converter_default_random_font():
    converter = AsciiArtConverter()
    result = await converter.convert_async(prompt="test", input_type="text")
    assert isinstance(result, ConverterResult)
    assert len(result.output_text) > 0


@pytest.mark.asyncio
async def test_ascii_art_converter_empty():
    converter = AsciiArtConverter(font="block")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_ascii_art_converter_input_not_supported():
    converter = AsciiArtConverter()
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")


def test_ascii_art_converter_input_supported():
    converter = AsciiArtConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_ascii_art_converter_output_supported():
    converter = AsciiArtConverter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
