# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, PortaConverter


async def test_porta_converter_key_a_is_rot13():
    # Row 0 of the Porta tableau (key letters A/B) maps A-M <-> N-Z, which is exactly ROT13.
    converter = PortaConverter(key="A")
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "uryyb"
    assert result.output_type == "text"


async def test_porta_converter_key_b_equals_key_a():
    # A and B select the same row (row 0), so they produce identical output.
    result_a = await PortaConverter(key="A").convert_async(prompt="hello", input_type="text")
    result_b = await PortaConverter(key="B").convert_async(prompt="hello", input_type="text")
    assert result_a.output_text == result_b.output_text


async def test_porta_converter_known_vector_key_c():
    # Hand-derived from the classic tableau, row 1 (key letters C/D):
    # H->V, E->S, L->Z, L->Z, O->A
    converter = PortaConverter(key="C")
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert result.output_text == "vszza"


async def test_porta_converter_is_reciprocal():
    # Encrypting twice with the same key recovers the plaintext.
    converter = PortaConverter(key="porta")
    plaintext = "The quick brown fox jumps over the lazy dog!"
    once = await converter.convert_async(prompt=plaintext, input_type="text")
    twice = await converter.convert_async(prompt=once.output_text, input_type="text")
    assert twice.output_text == plaintext


async def test_porta_converter_reciprocal_repeating_key():
    # Reciprocity holds for a multi-letter repeating key as well.
    converter = PortaConverter(key="SECRET")
    plaintext = "attackatdawn"
    once = await converter.convert_async(prompt=plaintext, input_type="text")
    twice = await converter.convert_async(prompt=once.output_text, input_type="text")
    assert once.output_text != plaintext
    assert twice.output_text == plaintext


async def test_porta_converter_preserves_case():
    converter = PortaConverter(key="A")
    result = await converter.convert_async(prompt="Hello", input_type="text")
    # ROT13 of "Hello" is "Uryyb" with case preserved.
    assert result.output_text == "Uryyb"


async def test_porta_converter_key_case_insensitive():
    result_lower = await PortaConverter(key="key").convert_async(prompt="hello", input_type="text")
    result_upper = await PortaConverter(key="KEY").convert_async(prompt="hello", input_type="text")
    assert result_lower.output_text == result_upper.output_text


async def test_porta_converter_non_alphabetic_passthrough():
    converter = PortaConverter(key="key")
    result = await converter.convert_async(prompt="hi there! 123", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text.count(" ") == "hi there! 123".count(" ")
    assert "123" in result.output_text
    assert "!" in result.output_text


async def test_porta_converter_non_alphabetic_does_not_advance_key():
    # key "AC": 'A' -> row 0, 'C' -> row 1.
    # The first 'a' uses row 0; the space passes through without consuming a key letter;
    # the second 'a' then uses row 1 (key letter 'C').
    converter = PortaConverter(key="AC")
    result = await converter.convert_async(prompt="a a", input_type="text")
    # row 0: a(0) -> (0+0)%13+13 = 13 -> 'n'; row 1: a(0) -> (0+1)%13+13 = 14 -> 'o'
    assert result.output_text == "n o"

    # Without the space, "aa" aligns identically (row 0 then row 1), confirming the space added no shift.
    result_no_space = await converter.convert_async(prompt="aa", input_type="text")
    assert result_no_space.output_text == "no"


async def test_porta_converter_non_ascii_alphabetic_passthrough():
    # Non-ASCII letters are not part of the cipher alphabet and must pass through unchanged.
    converter = PortaConverter(key="key")
    result = await converter.convert_async(prompt="café résumé", input_type="text")
    assert isinstance(result, ConverterResult)
    assert "é" in result.output_text


def test_porta_converter_invalid_non_ascii_key():
    with pytest.raises(ValueError, match="porta key value invalid"):
        PortaConverter(key="kéy")


def test_porta_converter_invalid_empty_key():
    with pytest.raises(ValueError, match="porta key value invalid"):
        PortaConverter(key="")


def test_porta_converter_invalid_non_alphabetic_key():
    with pytest.raises(ValueError, match="porta key value invalid"):
        PortaConverter(key="key123")


async def test_porta_converter_empty_prompt():
    converter = PortaConverter(key="key")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_porta_converter_input_not_supported():
    converter = PortaConverter(key="key")
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
