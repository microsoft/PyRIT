# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult
from pyrit.prompt_converter.diacritic_converter import DiacriticConverter


@pytest.mark.asyncio
async def test_convert_async_default_target_chars():
    converter = DiacriticConverter()
    text = "Hello, world!"
    result = await converter.convert_async(prompt=text)
    assert isinstance(result, ConverterResult)
    assert result.output_text == "Hélló, wórld!"  # Accents on every 'e', 'o' instance


@pytest.mark.asyncio
async def test_convert_async_custom_target_chars():
    converter = DiacriticConverter(target_chars="l")
    text = "Hello, world!"
    result = await converter.convert_async(prompt=text)
    assert result.output_text == "Heĺĺo, worĺd!"  # Accents on every 'l'


@pytest.mark.asyncio
async def test_convert_async_no_matching_chars():
    converter = DiacriticConverter(target_chars="xyz")
    text = "Hello, world!"
    result = await converter.convert_async(prompt=text)
    assert result.output_text == text  # No characters matched in "Hello, world!"


@pytest.mark.asyncio
async def test_convert_async_grave_accent():
    converter = DiacriticConverter(target_chars="aeiou", accent="grave")
    text = "Hello, world!"
    result = await converter.convert_async(prompt=text)
    assert result.output_text == "Hèllò, wòrld!"  # Grave accents on every 'e' and 'o'


@pytest.mark.asyncio
async def test_convert_async_invalid_accent():
    with pytest.raises(ValueError) as excinfo:
        converter = DiacriticConverter(accent="invalid")
        await converter.convert_async(prompt="Hello, world!")
    assert "Accent 'invalid' not recognized" in str(excinfo.value)


@pytest.mark.asyncio
async def test_convert_async_non_text_input_type():
    converter = DiacriticConverter()
    with pytest.raises(ValueError) as excinfo:
        await converter.convert_async(prompt="Hello, world!", input_type="non-text")
    assert "Only 'text' input type is supported" in str(excinfo.value)


def test_get_accent_mark_valid():
    converter = DiacriticConverter(accent="tilde")
    assert converter._get_accent_mark() == "\u0303"  # Tilde mark


def test_get_accent_mark_invalid():
    converter = DiacriticConverter(accent="invalid")
    with pytest.raises(ValueError) as excinfo:
        converter._get_accent_mark()
    assert "Accent 'invalid' not recognized" in str(excinfo.value)


@pytest.mark.asyncio
async def test_convert_async_single_character():
    converter = DiacriticConverter()
    text = "o"
    result = await converter.convert_async(prompt=text)
    assert result.output_text == "ó"  # 'o' with acute accent


@pytest.mark.asyncio
async def test_convert_async_whitespace_handling():
    converter = DiacriticConverter()
    text = "     "
    result = await converter.convert_async(prompt=text)
    assert result.output_text == text  # Whitespace should remain unchanged


@pytest.mark.asyncio
async def test_convert_async_empty_prompt():
    converter = DiacriticConverter()
    text = ""
    result = await converter.convert_async(prompt=text)
    assert result.output_text == ""  # Output should be empty if input is empty


def test_empty_target_chars():
    with pytest.raises(ValueError) as excinfo:
        DiacriticConverter(target_chars="")
    assert "target_chars cannot be empty." in str(excinfo.value)
