# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, NatoConverter


@pytest.mark.asyncio
async def test_nato_converter_simple_text():
    """Test basic NATO phonetic alphabet conversion."""
    converter = NatoConverter()
    prompt = "abc"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "Alfa Bravo Charlie"


@pytest.mark.asyncio
async def test_nato_converter_uppercase():
    """Test NATO conversion with uppercase letters."""
    converter = NatoConverter()
    prompt = "ABC"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "Alfa Bravo Charlie"


@pytest.mark.asyncio
async def test_nato_converter_mixed_case():
    """Test NATO conversion with mixed case letters."""
    converter = NatoConverter()
    prompt = "HeLLo"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "Hotel Echo Lima Lima Oscar"


@pytest.mark.asyncio
async def test_nato_converter_with_numbers():
    """Test that numbers are ignored in NATO conversion."""
    converter = NatoConverter()
    prompt = "a1b2c3"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Only letters should be converted
    assert result.output_text == "Alfa Bravo Charlie"


@pytest.mark.asyncio
async def test_nato_converter_with_spaces():
    """Test that spaces are ignored in NATO conversion."""
    converter = NatoConverter()
    prompt = "a b c"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Spaces should be ignored
    assert result.output_text == "Alfa Bravo Charlie"


@pytest.mark.asyncio
async def test_nato_converter_with_punctuation():
    """Test that punctuation is ignored in NATO conversion."""
    converter = NatoConverter()
    prompt = "Hello, world!"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Only letters should be converted
    assert result.output_text == "Hotel Echo Lima Lima Oscar Whiskey Oscar Romeo Lima Delta"


@pytest.mark.asyncio
async def test_nato_converter_empty_string():
    """Test NATO conversion with empty string."""
    converter = NatoConverter()
    prompt = ""

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == ""


@pytest.mark.asyncio
async def test_nato_converter_no_letters():
    """Test NATO conversion with no alphabetic characters."""
    converter = NatoConverter()
    prompt = "123!@#"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == ""


@pytest.mark.asyncio
async def test_nato_converter_all_letters():
    """Test NATO conversion with all letters of the alphabet."""
    converter = NatoConverter()
    prompt = "abcdefghijklmnopqrstuvwxyz"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    expected = (
        "Alfa Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliett "
        "Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra Tango "
        "Uniform Victor Whiskey Xray Yankee Zulu"
    )
    assert result.output_text == expected


@pytest.mark.asyncio
async def test_nato_converter_input_type_not_supported():
    """Test that non-text input types raise ValueError."""
    converter = NatoConverter()

    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")


def test_nato_converter_input_supported():
    """Test input_supported method."""
    converter = NatoConverter()

    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
    assert converter.input_supported("audio_path") is False


def test_nato_converter_output_supported():
    """Test output_supported method."""
    converter = NatoConverter()

    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
    assert converter.output_supported("audio_path") is False
