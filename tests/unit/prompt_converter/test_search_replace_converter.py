# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, SearchReplaceConverter


@pytest.mark.asyncio
async def test_search_replace_basic():
    converter = SearchReplaceConverter(pattern="hello", replace="world")
    result = await converter.convert_async(prompt="hello there", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "world there"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_search_replace_regex():
    converter = SearchReplaceConverter(pattern=r"\d+", replace="NUM")
    result = await converter.convert_async(prompt="abc123def456", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "abcNUMdefNUM"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_search_replace_list_replacement():
    converter = SearchReplaceConverter(pattern="hello", replace=["X"])
    result = await converter.convert_async(prompt="hello there", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "X there"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_search_replace_no_match():
    converter = SearchReplaceConverter(pattern="xyz", replace="abc")
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "hello"
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_search_replace_empty_string():
    converter = SearchReplaceConverter(pattern="hello", replace="world")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_search_replace_input_not_supported():
    converter = SearchReplaceConverter(pattern="hello", replace="world")
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
