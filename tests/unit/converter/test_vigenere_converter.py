# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, VigenereConverter


async def test_vigenere_converter_basic():
    converter = VigenereConverter(key="key")
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "rijvs"
    assert result.output_type == "text"


async def test_vigenere_converter_preserves_case():
    converter = VigenereConverter(key="key")
    result = await converter.convert_async(prompt="Hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "Rijvs"
    assert result.output_type == "text"


async def test_vigenere_converter_key_case_insensitive():
    converter_lower = VigenereConverter(key="key")
    converter_upper = VigenereConverter(key="KEY")
    result_lower = await converter_lower.convert_async(prompt="hello", input_type="text")
    result_upper = await converter_upper.convert_async(prompt="hello", input_type="text")
    assert result_lower.output_text == result_upper.output_text


async def test_vigenere_converter_non_alphabetic_passthrough():
    converter = VigenereConverter(key="key")
    result = await converter.convert_async(prompt="hi there! 123", input_type="text")
    assert isinstance(result, ConverterResult)
    # spaces, digits, and punctuation should be unchanged and should not consume a key position
    assert result.output_text.count(" ") == "hi there! 123".count(" ")
    assert "123" in result.output_text
    assert "!" in result.output_text


async def test_vigenere_converter_non_alphabetic_does_not_advance_key():
    converter = VigenereConverter(key="ab")
    # The first 'a' aligns with key position 0 ('a', shift 0) -> 'a'.
    # The space is passed through and does NOT consume a key position.
    # The second 'a' then aligns with key position 1 ('b', shift 1) -> 'b'.
    result = await converter.convert_async(prompt="a a", input_type="text")
    assert result.output_text == "a b"

    # Contrast: without the space in between, "aa" would align identically
    # (position 0 then position 1), confirming the space truly added no shift.
    result_no_space = await converter.convert_async(prompt="aa", input_type="text")
    assert result_no_space.output_text == "ab"


async def test_vigenere_converter_wraps_around():
    converter = VigenereConverter(key="z")
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "z"


async def test_vigenere_converter_with_description():
    converter = VigenereConverter(key="key", append_description=True)
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # The encoded prompt should be present in the output
    assert "rijvs" in result.output_text


async def test_vigenere_converter_non_ascii_alphabetic_passthrough():
    # Non-ASCII letters (e.g. accented characters) are alphabetic per str.isalpha() but are not
    # part of the cipher's alphabet; they must pass through unchanged rather than raising, matching
    # the behavior of CaesarConverter/AtbashConverter (which use str.translate() and silently skip
    # characters outside the translation table).
    converter = VigenereConverter(key="key")
    result = await converter.convert_async(prompt="café résumé", input_type="text")
    assert isinstance(result, ConverterResult)
    assert "é" in result.output_text


def test_vigenere_converter_invalid_non_ascii_key():
    with pytest.raises(ValueError, match="vigenere key value invalid"):
        VigenereConverter(key="kéy")


def test_vigenere_converter_invalid_empty_key():
    with pytest.raises(ValueError, match="vigenere key value invalid"):
        VigenereConverter(key="")


def test_vigenere_converter_invalid_non_alphabetic_key():
    with pytest.raises(ValueError, match="vigenere key value invalid"):
        VigenereConverter(key="key123")


async def test_vigenere_converter_empty_prompt():
    converter = VigenereConverter(key="key")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_vigenere_converter_input_not_supported():
    converter = VigenereConverter(key="key")
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
