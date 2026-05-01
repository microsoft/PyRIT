# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, StringJoinConverter


async def test_string_join_default():
    converter = StringJoinConverter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "h-e-l-l-o"
    assert result.output_type == "text"


async def test_string_join_custom_separator():
    converter = StringJoinConverter(join_value=".")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "h.i"
    assert result.output_type == "text"


async def test_string_join_multi_word():
    converter = StringJoinConverter()
    result = await converter.convert_async(prompt="hi there", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "h-i t-h-e-r-e"
    assert result.output_type == "text"


async def test_string_join_empty():
    converter = StringJoinConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_string_join_single_char():
    converter = StringJoinConverter()
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "a"
    assert result.output_type == "text"


async def test_string_join_input_not_supported():
    converter = StringJoinConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
