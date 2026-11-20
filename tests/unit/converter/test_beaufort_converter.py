# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

import pytest

from pyrit.converter import BeaufortConverter, ConverterResult


async def test_beaufort_converter_basic():
    converter = BeaufortConverter(key="key")
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    # C = (K - P) mod 26 with repeating key k,e,y,k,e: hello -> danzq
    assert result.output_text == "danzq"
    assert result.output_type == "text"


async def test_beaufort_converter_preserves_case():
    converter = BeaufortConverter(key="key")
    result = await converter.convert_async(prompt="Hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "Danzq"
    assert result.output_type == "text"


async def test_beaufort_converter_key_case_insensitive():
    converter_lower = BeaufortConverter(key="key")
    converter_upper = BeaufortConverter(key="KEY")
    result_lower = await converter_lower.convert_async(prompt="hello", input_type="text")
    result_upper = await converter_upper.convert_async(prompt="hello", input_type="text")
    assert result_lower.output_text == result_upper.output_text


async def test_beaufort_converter_is_reciprocal():
    # Beaufort is a reciprocal cipher: encrypting twice with the same key restores the plaintext.
    converter = BeaufortConverter(key="lemon")
    plaintext = "The quick brown fox jumps over the lazy dog"
    once = await converter.convert_async(prompt=plaintext, input_type="text")
    twice = await converter.convert_async(prompt=once.output_text, input_type="text")
    assert twice.output_text == plaintext


async def test_beaufort_converter_reciprocal_preserves_case_and_symbols():
    converter = BeaufortConverter(key="secret")
    plaintext = "Attack at Dawn! 07:30 (zone-A)."
    once = await converter.convert_async(prompt=plaintext, input_type="text")
    twice = await converter.convert_async(prompt=once.output_text, input_type="text")
    assert twice.output_text == plaintext


async def test_beaufort_converter_non_alphabetic_passthrough():
    converter = BeaufortConverter(key="key")
    result = await converter.convert_async(prompt="hi there! 123", input_type="text")
    assert isinstance(result, ConverterResult)
    # spaces, digits, and punctuation should be unchanged and should not consume a key position
    assert result.output_text.count(" ") == "hi there! 123".count(" ")
    assert "123" in result.output_text
    assert "!" in result.output_text


async def test_beaufort_converter_non_alphabetic_does_not_advance_key():
    converter = BeaufortConverter(key="ab")
    # First 'a' aligns with key position 0 ('a'=0): C=(0-0)=0 -> 'a'.
    # The space is passed through and does NOT consume a key position.
    # Second 'a' then aligns with key position 1 ('b'=1): C=(1-0)=1 -> 'b'.
    result = await converter.convert_async(prompt="a a", input_type="text")
    assert result.output_text == "a b"

    # Without the space, "aa" aligns identically (position 0 then 1), confirming the
    # space truly added no shift.
    result_no_space = await converter.convert_async(prompt="aa", input_type="text")
    assert result_no_space.output_text == "ab"


async def test_beaufort_converter_wraps_around():
    # 'a' (P=0) with key 'a' (K=0): C=(0-0)=0 -> 'a'. 'b' with key 'a': C=(0-1)%26=25 -> 'z'.
    converter = BeaufortConverter(key="a")
    result = await converter.convert_async(prompt="ab", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "az"


async def test_beaufort_converter_matches_formula():
    # Exhaustively verify C = (K - P) mod 26 across the lowercase alphabet with a single-letter key.
    key_letter = "m"
    key_value = ord(key_letter) - ord("a")
    converter = BeaufortConverter(key=key_letter)
    result = await converter.convert_async(prompt=string.ascii_lowercase, input_type="text")
    expected = "".join(chr((key_value - i) % 26 + ord("a")) for i in range(26))
    assert result.output_text == expected


async def test_beaufort_converter_non_ascii_alphabetic_passthrough():
    # Non-ASCII letters are alphabetic per str.isalpha() but are not part of the cipher's alphabet;
    # they must pass through unchanged rather than raising.
    converter = BeaufortConverter(key="key")
    result = await converter.convert_async(prompt="café résumé", input_type="text")
    assert isinstance(result, ConverterResult)
    assert "é" in result.output_text


def test_beaufort_converter_invalid_non_ascii_key():
    with pytest.raises(ValueError, match="beaufort key value invalid"):
        BeaufortConverter(key="kéy")


def test_beaufort_converter_invalid_empty_key():
    with pytest.raises(ValueError, match="beaufort key value invalid"):
        BeaufortConverter(key="")


def test_beaufort_converter_invalid_non_alphabetic_key():
    with pytest.raises(ValueError, match="beaufort key value invalid"):
        BeaufortConverter(key="key123")


async def test_beaufort_converter_empty_prompt():
    converter = BeaufortConverter(key="key")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_beaufort_converter_input_not_supported():
    converter = BeaufortConverter(key="key")
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
