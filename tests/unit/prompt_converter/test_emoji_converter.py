# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, EmojiConverter


@pytest.mark.asyncio
async def test_emoji_converter_basic():
    converter = EmojiConverter()
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text in EmojiConverter.emoji_dict["a"]
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_emoji_converter_produces_output():
    converter = EmojiConverter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert len(result.output_text) > 0
    assert result.output_text != "hello"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_emoji_converter_empty():
    converter = EmojiConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_emoji_converter_numbers_unchanged():
    converter = EmojiConverter()
    result = await converter.convert_async(prompt="123", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "123"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_emoji_converter_input_not_supported():
    converter = EmojiConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
