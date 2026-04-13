# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, RandomCapitalLettersConverter


@pytest.mark.asyncio
async def test_random_capital_100_percent():
    converter = RandomCapitalLettersConverter(percentage=100.0)
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "HELLO"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_random_capital_preserves_non_alpha():
    converter = RandomCapitalLettersConverter(percentage=100.0)
    result = await converter.convert_async(prompt="hello123", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "HELLO123"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_random_capital_0_not_allowed():
    converter = RandomCapitalLettersConverter(percentage=0)
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="text")


@pytest.mark.asyncio
async def test_random_capital_partial():
    converter = RandomCapitalLettersConverter(percentage=50.0)
    result = await converter.convert_async(prompt="abcdefghij", input_type="text")
    assert isinstance(result, ConverterResult)
    assert len(result.output_text) == 10
    upper_count = sum(1 for c in result.output_text if c.isupper())
    assert upper_count > 0
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_random_capital_empty():
    converter = RandomCapitalLettersConverter(percentage=100.0)
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_random_capital_input_not_supported():
    converter = RandomCapitalLettersConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
