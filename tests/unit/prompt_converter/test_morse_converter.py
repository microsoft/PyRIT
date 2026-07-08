# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, MorseConverter


async def test_morse_converter_basic():
    converter = MorseConverter()
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ".... .."
    assert result.output_type == "text"


async def test_morse_converter_word_separator():
    converter = MorseConverter()
    result = await converter.convert_async(prompt="hi hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ".... .. / .... .."
    assert result.output_type == "text"


async def test_morse_converter_uppercase():
    converter = MorseConverter()
    result = await converter.convert_async(prompt="HI", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ".... .."
    assert result.output_type == "text"


async def test_morse_converter_with_description():
    converter = MorseConverter(append_description=True)
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # The encoded prompt should be present in the output
    assert ".... .." in result.output_text


async def test_morse_converter_empty():
    converter = MorseConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_morse_converter_input_not_supported():
    converter = MorseConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


async def test_morse_converter_extended_ae_ligature():
    # Regression: the extended mapping key for Æ previously contained a stray
    # leading space (" Æ"), so any prompt containing 'æ'/'Æ' raised KeyError.
    converter = MorseConverter()
    result = await converter.convert_async(prompt="æ", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ".-.-"
    assert result.output_type == "text"


async def test_morse_converter_extended_ae_in_word():
    converter = MorseConverter()
    result = await converter.convert_async(prompt="Æther", input_type="text")
    assert isinstance(result, ConverterResult)
    # Æ T H E R
    assert result.output_text == ".-.- - .... . .-."
    assert result.output_type == "text"
