# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, FlipConverter


async def test_flip_converter_basic():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "olleh"
    assert result.output_type == "text"


async def test_flip_converter_sentence():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="hello world", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "dlrow olleh"
    assert result.output_type == "text"


async def test_flip_converter_empty():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_flip_converter_single_char():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "a"
    assert result.output_type == "text"


async def test_flip_converter_palindrome():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="racecar", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "racecar"
    assert result.output_type == "text"


async def test_flip_converter_input_not_supported():
    converter = FlipConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
