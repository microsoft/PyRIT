# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import BinaryConverter, ConverterResult
from pyrit.converter.text_selection_strategy import WordIndexSelectionStrategy


async def test_binary_converter_8_bit_ascii():
    converter = BinaryConverter(bits_per_char=BinaryConverter.BitsPerChar.BITS_8)
    prompt = "A"
    expected_output = "01000001"  # 8-bit binary representation of 'A'
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == expected_output
    assert result.output_type == "text"


async def test_binary_converter_16_bit_unicode():
    converter = BinaryConverter(bits_per_char=BinaryConverter.BitsPerChar.BITS_16)
    prompt = "é"  # Unicode character with code point U+00E9
    expected_output = "0000000011101001"  # 16-bit binary representation of 'é'
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == expected_output
    assert result.output_type == "text"


async def test_binary_converter_32_bit_emoji():
    converter = BinaryConverter(bits_per_char=BinaryConverter.BitsPerChar.BITS_32)
    prompt = "😊"  # Emoji character with code point U+1F60A
    expected_output = "00000000000000011111011000001010"  # 32-bit binary representation of '😊'
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == expected_output
    assert result.output_type == "text"


async def test_binary_converter_invalid_bits_per_char():
    with pytest.raises(TypeError, match="bits_per_char must be an instance of BinaryConverter.BitsPerChar Enum."):
        BinaryConverter(bits_per_char=10)  # Invalid bits_per_char


async def test_binary_converter_raises_when_selected_word_exceeds_bits():
    converter = BinaryConverter(bits_per_char=BinaryConverter.BitsPerChar.BITS_16)
    with pytest.raises(ValueError, match="bits_per_char=16 is too small"):
        await converter.convert_async(prompt="hello 👋", input_type="text")


async def test_binary_converter_ignores_unselected_word_exceeding_bits():
    # Only "hello" is converted, so the emoji in the unselected word is passed
    # through untouched and must not fail validation.
    converter = BinaryConverter(
        bits_per_char=BinaryConverter.BitsPerChar.BITS_16,
        word_selection_strategy=WordIndexSelectionStrategy(indices=[0]),
    )
    result = await converter.convert_async(prompt="hello 👋", input_type="text")
    expected_hello = " ".join(format(ord(char), "016b") for char in "hello")
    space_binary = format(ord(" "), "016b")
    assert result.output_text == f"{expected_hello} {space_binary} 👋"
    assert result.output_type == "text"
