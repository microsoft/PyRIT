# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

import pytest

from pyrit.converter import CharacterSpaceConverter, ConverterResult


async def test_character_space_basic():
    converter = CharacterSpaceConverter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "h e l l o"
    assert result.output_type == "text"


async def test_character_space_removes_punctuation():
    converter = CharacterSpaceConverter()
    result = await converter.convert_async(prompt="hello!", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "h e l l o"
    assert "!" not in result.output_text
    assert result.output_type == "text"


async def test_character_space_multiple_punctuation():
    converter = CharacterSpaceConverter()
    result = await converter.convert_async(prompt="attack... now", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "a t t a c k   n o w"
    assert result.output_type == "text"


async def test_character_space_pure_punctuation():
    converter = CharacterSpaceConverter()
    result = await converter.convert_async(prompt="!!!", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.parametrize("punctuation", string.punctuation.replace("\\", ""))
async def test_character_space_punctuation_charset_async(*, punctuation: str) -> None:
    converter = CharacterSpaceConverter()
    result = await converter.convert_async(prompt=f"a{punctuation}b")
    assert result.output_text == "a b"
    assert result.output_type == "text"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("\\", "\\"),
        (r"a\b", r"a \ b"),
        (r"C:\temp\file.txt", r"C \ t e m p \ f i l e t x t"),
        (r"\n", r"\ n"),
    ],
)
async def test_character_space_preserves_backslashes_async(*, prompt: str, expected: str) -> None:
    converter = CharacterSpaceConverter()
    result = await converter.convert_async(prompt=prompt)
    assert result.output_text == expected
    assert result.output_type == "text"


async def test_character_space_with_spaces():
    converter = CharacterSpaceConverter()
    result = await converter.convert_async(prompt="hi there", input_type="text")
    assert isinstance(result, ConverterResult)
    # "hi there" -> " ".join -> "h i   t h e r e" (3 spaces between i and t)
    assert result.output_text == "h i   t h e r e"
    assert result.output_type == "text"


async def test_character_space_empty():
    converter = CharacterSpaceConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_character_space_input_not_supported():
    converter = CharacterSpaceConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
