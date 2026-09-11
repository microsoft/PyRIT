# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, NatoConverter
from pyrit.converter.text_selection_strategy import WordIndexSelectionStrategy


async def test_nato_converter_simple_text():
    """Test basic NATO phonetic alphabet conversion."""
    converter = NatoConverter()
    prompt = "abc"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "Alfa Bravo Charlie"


async def test_nato_converter_uppercase():
    """Test NATO conversion with uppercase letters."""
    converter = NatoConverter()
    prompt = "ABC"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "Alfa Bravo Charlie"


async def test_nato_converter_mixed_case():
    """Test NATO conversion with mixed case letters."""
    converter = NatoConverter()
    prompt = "HeLLo"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "Hotel Echo Lima Lima Oscar"


async def test_nato_converter_with_numbers():
    """Test that numbers are preserved in NATO conversion."""
    converter = NatoConverter()
    prompt = "a1b2c3"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Digits are preserved as-is so the encoded prompt keeps its full content
    assert result.output_text == "Alfa 1 Bravo 2 Charlie 3"


async def test_nato_converter_with_spaces():
    """Test that word boundaries remain distinct from code-word separators."""
    converter = NatoConverter()
    prompt = "a b c"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "Alfa <space> Bravo <space> Charlie"


async def test_nato_converter_with_punctuation():
    """Test that punctuation is preserved in NATO conversion."""
    converter = NatoConverter()
    prompt = "Hello, world!"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Punctuation is preserved as-is so the encoded prompt keeps its full content
    assert result.output_text == "Hotel Echo Lima Lima Oscar , <space> Whiskey Oscar Romeo Lima Delta !"


async def test_nato_converter_empty_string():
    """Test NATO conversion with empty string."""
    converter = NatoConverter()
    prompt = ""

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == ""


async def test_nato_converter_no_letters():
    """Test NATO conversion with no alphabetic characters.

    Regression: non-empty input must never convert to an empty prompt
    (digits/punctuation are preserved rather than erased).
    """
    converter = NatoConverter()
    prompt = "123!@#"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "1 2 3 ! @ #"


async def test_nato_converter_space_only_prompt():
    """Test that a non-empty whitespace prompt remains non-empty."""
    converter = NatoConverter()

    result = await converter.convert_async(prompt="   ", input_type="text")

    assert result.output_text == "<space> <space> <space>"


@pytest.mark.parametrize("prompt", ["é", "ß", "ı", "ñ"])
async def test_nato_converter_preserves_unmapped_unicode(prompt: str):
    """Test that Unicode characters are preserved without case conversion."""
    converter = NatoConverter()

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert result.output_text == prompt


async def test_nato_converter_word_selection_strategy():
    """Test that NATO conversion supports the shared word selection strategies."""
    converter = NatoConverter(word_selection_strategy=WordIndexSelectionStrategy(indices=[1]))

    result = await converter.convert_async(prompt="abc def", input_type="text")

    assert result.output_text == "abc <space> Delta Echo Foxtrot"


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


async def test_nato_converter_input_type_not_supported():
    """Test that non-text input types raise ValueError."""
    converter = NatoConverter()

    with pytest.raises(ValueError, match="Input type image_path not supported"):
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
