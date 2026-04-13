# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import AtbashConverter, ConverterResult


@pytest.mark.asyncio
async def test_atbash_converter_basic():
    converter = AtbashConverter()
    result = await converter.convert_async(prompt="abc", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "zyx"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_atbash_converter_uppercase():
    converter = AtbashConverter()
    result = await converter.convert_async(prompt="ABC", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "ZYX"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_atbash_converter_mixed():
    converter = AtbashConverter()
    result = await converter.convert_async(prompt="Hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "Svool"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_atbash_converter_with_description():
    converter = AtbashConverter(append_description=True)
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # The encoded prompt should be present in the output
    assert "svool" in result.output_text


@pytest.mark.asyncio
async def test_atbash_converter_empty_string():
    converter = AtbashConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_atbash_converter_numbers():
    converter = AtbashConverter()
    result = await converter.convert_async(prompt="012", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "987"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_atbash_converter_input_not_supported():
    converter = AtbashConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


def test_atbash_converter_input_supported():
    converter = AtbashConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_atbash_converter_output_supported():
    converter = AtbashConverter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
