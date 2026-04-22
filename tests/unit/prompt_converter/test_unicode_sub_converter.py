# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, UnicodeSubstitutionConverter


@pytest.mark.asyncio
async def test_unicode_sub_basic():
    converter = UnicodeSubstitutionConverter()
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == chr(0xE0000 + ord("a"))
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_unicode_sub_custom_start():
    converter = UnicodeSubstitutionConverter(start_value=0x1F600)
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == chr(0x1F600 + ord("a"))
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_unicode_sub_empty():
    converter = UnicodeSubstitutionConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_unicode_sub_multiple_chars():
    converter = UnicodeSubstitutionConverter()
    result = await converter.convert_async(prompt="ab", input_type="text")
    assert isinstance(result, ConverterResult)
    expected = chr(0xE0000 + ord("a")) + chr(0xE0000 + ord("b"))
    assert result.output_text == expected
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_unicode_sub_input_not_supported():
    converter = UnicodeSubstitutionConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
