# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import Base91Converter, ConverterResult


async def test_base91_converter_known_vector():
    # Reference basE91 vector (Joachim Henke's basE91): "test" -> "fPNKd".
    converter = Base91Converter()
    result = await converter.convert_async(prompt="test", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "fPNKd"
    assert result.output_type == "text"


async def test_base91_converter_hello_world():
    # Reference basE91 vector: "Hello World!" -> ">OwJh>Io0Tv!8PE".
    converter = Base91Converter()
    result = await converter.convert_async(prompt="Hello World!", input_type="text")
    assert result.output_text == ">OwJh>Io0Tv!8PE"


async def test_base91_converter_single_char():
    # A single byte flushes with one output char (0x61 = 'a' -> value 97, 97 % 91 = 6 -> 'G'; 97 // 91 = 1 -> 'B').
    converter = Base91Converter()
    result = await converter.convert_async(prompt="a", input_type="text")
    assert result.output_text == "GB"


async def test_base91_converter_empty():
    converter = Base91Converter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_base91_converter_uses_only_alphabet():
    converter = Base91Converter()
    result = await converter.convert_async(prompt="The quick brown fox jumps over 13 lazy dogs!", input_type="text")
    assert all(ch in Base91Converter.ALPHABET for ch in result.output_text)


async def test_base91_converter_alphabet_is_91_unique_chars():
    assert len(Base91Converter.ALPHABET) == 91
    assert len(set(Base91Converter.ALPHABET)) == 91


async def test_base91_converter_unicode_input():
    # Multi-byte UTF-8 input encodes without error and stays within the alphabet.
    converter = Base91Converter()
    result = await converter.convert_async(prompt="café ☕", input_type="text")
    assert result.output_text != ""
    assert all(ch in Base91Converter.ALPHABET for ch in result.output_text)


async def test_base91_converter_input_not_supported():
    converter = Base91Converter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


def test_base91_converter_input_supported():
    converter = Base91Converter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_base91_converter_output_supported():
    converter = Base91Converter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
