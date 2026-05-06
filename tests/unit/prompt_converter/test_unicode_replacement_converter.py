# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, UnicodeReplacementConverter


async def test_unicode_replacement_basic():
    converter = UnicodeReplacementConverter()
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "\\u0061"
    assert result.output_type == "text"


async def test_unicode_replacement_word():
    converter = UnicodeReplacementConverter()
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "\\u0068\\u0069"
    assert result.output_type == "text"


async def test_unicode_replacement_with_spaces():
    converter = UnicodeReplacementConverter(encode_spaces=False)
    result = await converter.convert_async(prompt="a b", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "\\u0061 \\u0062"
    assert result.output_type == "text"


async def test_unicode_replacement_encode_spaces():
    converter = UnicodeReplacementConverter(encode_spaces=True)
    result = await converter.convert_async(prompt="a b", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "\\u0061\\u0020\\u0062"
    assert result.output_type == "text"


async def test_unicode_replacement_empty():
    converter = UnicodeReplacementConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_unicode_replacement_input_not_supported():
    converter = UnicodeReplacementConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
