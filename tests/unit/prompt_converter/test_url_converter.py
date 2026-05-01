# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, UrlConverter


async def test_url_converter_basic():
    converter = UrlConverter()
    result = await converter.convert_async(prompt="hello world", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "hello%20world"
    assert result.output_type == "text"


async def test_url_converter_special_chars():
    converter = UrlConverter()
    result = await converter.convert_async(prompt="a&b=c", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "a%26b%3Dc"
    assert result.output_type == "text"


async def test_url_converter_already_safe():
    converter = UrlConverter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "hello"
    assert result.output_type == "text"


async def test_url_converter_empty():
    converter = UrlConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_url_converter_input_not_supported():
    converter = UrlConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
