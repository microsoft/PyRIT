# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import CaesarConverter, ConverterResult


@pytest.mark.asyncio
async def test_caesar_converter_shift_1():
    converter = CaesarConverter(caesar_offset=1)
    result = await converter.convert_async(prompt="abc", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "bcd"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_caesar_converter_shift_negative():
    converter = CaesarConverter(caesar_offset=-1)
    result = await converter.convert_async(prompt="bcd", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "abc"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_caesar_converter_wraps_around():
    converter = CaesarConverter(caesar_offset=1)
    result = await converter.convert_async(prompt="xyz", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "yza"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_caesar_converter_preserves_case():
    converter = CaesarConverter(caesar_offset=1)
    result = await converter.convert_async(prompt="AbC", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "BcD"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_caesar_converter_with_description():
    converter = CaesarConverter(caesar_offset=1, append_description=True)
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # The encoded prompt should be present in the output
    assert "ifmmp" in result.output_text


def test_caesar_converter_invalid_offset():
    with pytest.raises(ValueError, match="caesar offset value invalid"):
        CaesarConverter(caesar_offset=26)


def test_caesar_converter_invalid_negative_offset():
    with pytest.raises(ValueError, match="caesar offset value invalid"):
        CaesarConverter(caesar_offset=-26)


@pytest.mark.asyncio
async def test_caesar_converter_empty():
    converter = CaesarConverter(caesar_offset=1)
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_caesar_converter_input_not_supported():
    converter = CaesarConverter(caesar_offset=1)
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
