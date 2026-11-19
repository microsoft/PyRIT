# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, GronsfeldConverter


async def test_gronsfeld_converter_basic():
    converter = GronsfeldConverter(key="123")
    result = await converter.convert_async(prompt="Hello", input_type="text")
    assert isinstance(result, ConverterResult)
    # H+1=I, e+2=g, l+3=o, l+1=m, o+2=q
    assert result.output_text == "Igomq"
    assert result.output_type == "text"


async def test_gronsfeld_converter_preserves_case():
    converter = GronsfeldConverter(key="123")
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert result.output_text == "igomq"


async def test_gronsfeld_converter_list_key_matches_string_key():
    converter_str = GronsfeldConverter(key="123")
    converter_list = GronsfeldConverter(key=[1, 2, 3])
    result_str = await converter_str.convert_async(prompt="Hello", input_type="text")
    result_list = await converter_list.convert_async(prompt="Hello", input_type="text")
    assert result_str.output_text == result_list.output_text == "Igomq"


async def test_gronsfeld_converter_default_key():
    # Default key "31415" applied to "attack".
    converter = GronsfeldConverter()
    result = await converter.convert_async(prompt="attack", input_type="text")
    # a+3=d, t+1=u, t+4=x, a+1=b, c+5=h, k+3=n
    assert result.output_text == "duxbhn"


async def test_gronsfeld_converter_zero_shift_is_identity():
    converter = GronsfeldConverter(key="0")
    result = await converter.convert_async(prompt="Hello, World!", input_type="text")
    assert result.output_text == "Hello, World!"


async def test_gronsfeld_converter_wraps_around():
    converter = GronsfeldConverter(key="5")
    result = await converter.convert_async(prompt="vwxyz", input_type="text")
    assert result.output_text == "abcde"


async def test_gronsfeld_converter_non_alphabetic_passthrough():
    converter = GronsfeldConverter(key="123")
    result = await converter.convert_async(prompt="hi there! 123", input_type="text")
    assert result.output_text.count(" ") == "hi there! 123".count(" ")
    assert "123" in result.output_text
    assert "!" in result.output_text


async def test_gronsfeld_converter_non_alphabetic_does_not_advance_key():
    converter = GronsfeldConverter(key="12")
    # First 'a' aligns with key position 0 (shift 1) -> 'b'.
    # The space is passed through and does NOT consume a key position.
    # Second 'a' then aligns with key position 1 (shift 2) -> 'c'.
    result = await converter.convert_async(prompt="a a", input_type="text")
    assert result.output_text == "b c"

    result_no_space = await converter.convert_async(prompt="aa", input_type="text")
    assert result_no_space.output_text == "bc"


async def test_gronsfeld_converter_non_ascii_alphabetic_passthrough():
    # Non-ASCII letters are alphabetic per str.isalpha() but are not part of the cipher's
    # ASCII alphabet; they must pass through unchanged rather than raising.
    converter = GronsfeldConverter(key="123")
    result = await converter.convert_async(prompt="café résumé", input_type="text")
    assert "é" in result.output_text


def test_gronsfeld_converter_identifier_includes_key():
    converter = GronsfeldConverter(key=[3, 1, 4, 1, 5])
    # List keys are normalized to their digit-string representation for the identifier.
    assert converter.key == "31415"


def test_gronsfeld_converter_invalid_empty_string_key():
    with pytest.raises(ValueError, match="gronsfeld key value invalid"):
        GronsfeldConverter(key="")


def test_gronsfeld_converter_invalid_empty_list_key():
    with pytest.raises(ValueError, match="gronsfeld key value invalid"):
        GronsfeldConverter(key=[])


def test_gronsfeld_converter_invalid_non_digit_string_key():
    with pytest.raises(ValueError, match="gronsfeld key value invalid"):
        GronsfeldConverter(key="12a")


def test_gronsfeld_converter_invalid_out_of_range_list_key():
    with pytest.raises(ValueError, match="gronsfeld key value invalid"):
        GronsfeldConverter(key=[1, 2, 10])


def test_gronsfeld_converter_invalid_bool_in_list_key():
    with pytest.raises(ValueError, match="gronsfeld key value invalid"):
        GronsfeldConverter(key=[True, 2])


async def test_gronsfeld_converter_empty_prompt():
    converter = GronsfeldConverter(key="123")
    result = await converter.convert_async(prompt="", input_type="text")
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_gronsfeld_converter_input_not_supported():
    converter = GronsfeldConverter(key="123")
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
