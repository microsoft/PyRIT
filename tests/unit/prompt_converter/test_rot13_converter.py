# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, ROT13Converter


@pytest.mark.asyncio
async def test_rot13_converter_basic():
    converter = ROT13Converter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "uryyb"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_rot13_converter_roundtrip():
    converter = ROT13Converter()
    first = await converter.convert_async(prompt="hello", input_type="text")
    second = await converter.convert_async(prompt=first.output_text, input_type="text")
    assert second.output_text == "hello"


@pytest.mark.asyncio
async def test_rot13_converter_uppercase():
    converter = ROT13Converter()
    result = await converter.convert_async(prompt="ABC", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "NOP"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_rot13_converter_with_numbers():
    converter = ROT13Converter()
    result = await converter.convert_async(prompt="hello123", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "uryyb123"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_rot13_converter_empty():
    converter = ROT13Converter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_rot13_converter_input_not_supported():
    converter = ROT13Converter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
