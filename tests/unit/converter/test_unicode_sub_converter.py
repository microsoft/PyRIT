# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, UnicodeSubstitutionConverter


async def test_unicode_sub_basic():
    converter = UnicodeSubstitutionConverter()
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == chr(0xE0000 + ord("a"))
    assert result.output_type == "text"


async def test_unicode_sub_custom_start():
    converter = UnicodeSubstitutionConverter(start_value=0x1F600)
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == chr(0x1F600 + ord("a"))
    assert result.output_type == "text"


@pytest.mark.parametrize("start_value", [-1, 0x110000])
def test_unicode_sub_rejects_invalid_start_value(start_value):
    with pytest.raises(ValueError, match="valid Unicode code point"):
        UnicodeSubstitutionConverter(start_value=start_value)


async def test_unicode_sub_rejects_derived_code_point_overflow():
    converter = UnicodeSubstitutionConverter(start_value=0x10FFFF)

    with pytest.raises(ValueError, match="exceeds the maximum code point"):
        await converter.convert_async(prompt="a", input_type="text")


async def test_unicode_sub_empty():
    converter = UnicodeSubstitutionConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_unicode_sub_multiple_chars():
    converter = UnicodeSubstitutionConverter()
    result = await converter.convert_async(prompt="ab", input_type="text")
    assert isinstance(result, ConverterResult)
    expected = chr(0xE0000 + ord("a")) + chr(0xE0000 + ord("b"))
    assert result.output_text == expected
    assert result.output_type == "text"


async def test_unicode_sub_input_not_supported():
    converter = UnicodeSubstitutionConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
