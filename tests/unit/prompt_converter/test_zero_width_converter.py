# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, ZeroWidthConverter


@pytest.mark.asyncio
async def test_convert_async_injects_zero_width_spaces():
    converter = ZeroWidthConverter()
    text = "Hello"
    expected_output = "H\u200be\u200bl\u200bl\u200bo"  # Zero-width spaces between each character
    result = await converter.convert_async(prompt=text)
    assert isinstance(result, ConverterResult)
    assert result.output_text == expected_output  # Check if output matches expected result with zero-width spaces


@pytest.mark.asyncio
async def test_convert_async_long_text():
    converter = ZeroWidthConverter()
    text = "This is a longer text used to test the ZeroWidthConverter."
    # Expected output has a zero-width space between every character in `text`
    expected_output = "\u200b".join(text)

    result = await converter.convert_async(prompt=text)

    assert result.output_text == expected_output
    # Verify that the output length is as expected: original length + (number of characters - 1) for zero-width spaces
    assert len(result.output_text) == len(text) + len(text) - 1


@pytest.mark.asyncio
async def test_convert_async_handles_empty_string():
    converter = ZeroWidthConverter()
    text = ""
    result = await converter.convert_async(prompt=text)
    assert result.output_text == ""  # Output should be empty if input is empty


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_convert_async_single_character():
    converter = ZeroWidthConverter()
    text = "A"  # Single character input
    expected_output = "A"  # Should remain unchanged without zero-width spaces
    result = await converter.convert_async(prompt=text)
    assert result.output_text == expected_output


@pytest.mark.asyncio
async def test_convert_async_multiple_whitespace():
    converter = ZeroWidthConverter()
    text = "   "  # Input with multiple whitespace characters

    # Converter Behavior: The ZeroWidthConverter inserts zero-width spaces between each character,
    # resulting in N - 1 zero-width spaces for an input of length N.
    # For three spaces, there will be two zero-width spaces between them.
    expected_output = " \u200b \u200b "

    result = await converter.convert_async(prompt=text)
    assert result.output_text == expected_output, f"Unexpected output: {result.output_text}"
