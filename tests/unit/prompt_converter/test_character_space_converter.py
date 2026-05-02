# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import CharacterSpaceConverter, ConverterResult


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
    # "hello!" -> " ".join -> "h e l l o !" -> remove "!" -> "h e l l o "
    assert "h e l l o" in result.output_text
    assert "!" not in result.output_text
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
