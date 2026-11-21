# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, ZeroWidthConverter


async def test_convert_async_injects_zero_width_spaces():
    converter = ZeroWidthConverter()
    text = "Hello"
    expected_output = "H\u200be\u200bl\u200bl\u200bo"  # Zero-width spaces between each character
    result = await converter.convert_async(prompt=text)
    assert isinstance(result, ConverterResult)
    assert result.output_text == expected_output  # Check if output matches expected result with zero-width spaces


async def test_convert_async_long_text():
    converter = ZeroWidthConverter()
    text = "This is a longer text used to test the ZeroWidthConverter."
    # Expected output has a zero-width space between every character in `text`
    expected_output = "\u200b".join(text)

    result = await converter.convert_async(prompt=text)

    assert result.output_text == expected_output
    # Verify that the output length is as expected: original length + (number of characters - 1) for zero-width spaces
    assert len(result.output_text) == len(text) + len(text) - 1


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("cafe\u0301", "c\u200ba\u200bf\u200be\u0301"),
        ("\u0939\u093f\u0902\u0926\u0940", "\u0939\u093f\u0902\u200b\u0926\u0940"),
        ("\U0001f1ee\U0001f1f3\U0001f44d\U0001f3fd", "\U0001f1ee\U0001f1f3\u200b\U0001f44d\U0001f3fd"),
        ("\U0001f468\u200d\U0001f469\u200d\U0001f467ok", "\U0001f468\u200d\U0001f469\u200d\U0001f467\u200bo\u200bk"),
    ],
)
async def test_convert_async_keeps_grapheme_clusters(*, prompt: str, expected: str) -> None:
    result = await ZeroWidthConverter().convert_async(prompt=prompt)

    assert result.output_text == expected


async def test_convert_async_handles_empty_string():
    converter = ZeroWidthConverter()
    text = ""
    result = await converter.convert_async(prompt=text)
    assert result.output_text == ""  # Output should be empty if input is empty


async def test_convert_async_non_text_input_type():
    converter = ZeroWidthConverter()
    with pytest.raises(ValueError) as excinfo:
        await converter.convert_async(prompt="Hello", input_type="non-text")
    assert "Only 'text' input type is supported" in str(excinfo.value)


def test_input_supported_text():
    converter = ZeroWidthConverter()
    assert converter.input_supported("text") is True  # Should support 'text' input type


def test_input_supported_non_text():
    converter = ZeroWidthConverter()
    assert converter.input_supported("non-text") is False  # Should not support non-'text' input types


async def test_convert_async_single_character():
    converter = ZeroWidthConverter()
    text = "A"  # Single character input
    expected_output = "A"  # Should remain unchanged without zero-width spaces
    result = await converter.convert_async(prompt=text)
    assert result.output_text == expected_output


async def test_convert_async_multiple_whitespace():
    converter = ZeroWidthConverter()
    text = "   "  # Input with multiple whitespace characters

    # Converter Behavior: The ZeroWidthConverter inserts zero-width spaces between each character,
    # resulting in N - 1 zero-width spaces for an input of length N.
    # For three spaces, there will be two zero-width spaces between them.
    expected_output = " \u200b \u200b "

    result = await converter.convert_async(prompt=text)
    assert result.output_text == expected_output, f"Unexpected output: {result.output_text}"
