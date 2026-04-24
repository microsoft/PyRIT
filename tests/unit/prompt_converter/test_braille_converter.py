# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import BrailleConverter, ConverterResult


@pytest.mark.asyncio
async def test_braille_converter_simple_text():
    """Test basic Braille conversion."""
    converter = BrailleConverter()
    prompt = "hello"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Verify it returns some braille characters
    assert result.output_text != ""
    assert result.output_text != prompt


@pytest.mark.asyncio
async def test_braille_converter_with_space():
    """Test Braille conversion with spaces."""
    converter = BrailleConverter()
    prompt = "hi there"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Should preserve space
    assert " " in result.output_text


@pytest.mark.asyncio
async def test_braille_converter_uppercase():
    """Test Braille conversion with uppercase letters."""
    converter = BrailleConverter()
    prompt = "Hello"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Should have some output
    assert len(result.output_text) > 0


@pytest.mark.asyncio
async def test_braille_converter_numbers():
    """Test Braille conversion with numbers."""
    converter = BrailleConverter()
    prompt = "123"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) > 0


@pytest.mark.asyncio
async def test_braille_converter_consecutive_numbers_single_prefix():
    """Test that consecutive digits get only one number prefix."""
    converter = BrailleConverter()
    num_prefix = "\u283c"  # Braille number indicator

    result = await converter.convert_async(prompt="123", input_type="text")
    # Should have exactly one number prefix for a run of consecutive digits
    assert result.output_text.count(num_prefix) == 1

    result = await converter.convert_async(prompt="1.2", input_type="text")
    # Period is a number punctuation, so "1.2" stays in number mode — one prefix
    assert result.output_text.count(num_prefix) == 1


@pytest.mark.asyncio
async def test_braille_converter_punctuation():
    """Test Braille conversion with punctuation."""
    converter = BrailleConverter()
    prompt = "Hello, world!"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) > 0


@pytest.mark.asyncio
async def test_braille_converter_input_type_not_supported():
    """Test that non-text input types raise ValueError."""
    converter = BrailleConverter()

    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")


def test_braille_converter_input_supported():
    """Test input_supported method."""
    converter = BrailleConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_braille_converter_output_supported():
    """Test output_supported method."""
    converter = BrailleConverter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
