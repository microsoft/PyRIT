# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import CaesarConverter, ConverterResult


async def test_caesar_converter_shift_1():
    converter = CaesarConverter(caesar_offset=1)
    result = await converter.convert_async(prompt="abc", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "bcd"
    assert result.output_type == "text"


async def test_caesar_converter_shift_negative():
    converter = CaesarConverter(caesar_offset=-1)
    result = await converter.convert_async(prompt="bcd", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "abc"
    assert result.output_type == "text"


async def test_caesar_converter_wraps_around():
    converter = CaesarConverter(caesar_offset=1)
    result = await converter.convert_async(prompt="xyz", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "yza"
    assert result.output_type == "text"


async def test_caesar_converter_preserves_case():
    converter = CaesarConverter(caesar_offset=1)
    result = await converter.convert_async(prompt="AbC", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "BcD"
    assert result.output_type == "text"


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(3, "3456789012"), (13, "3456789012"), (25, "5678901234"), (-3, "7890123456"), (-13, "7890123456")],
)
async def test_caesar_converter_shifts_digits_by_offset_modulo_ten(offset, expected):
    converter = CaesarConverter(caesar_offset=offset)
    result = await converter.convert_async(prompt="0123456789", input_type="text")
    assert result.output_text == expected


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


async def test_caesar_converter_empty():
    converter = CaesarConverter(caesar_offset=1)
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_caesar_converter_input_not_supported():
    converter = CaesarConverter(caesar_offset=1)
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
