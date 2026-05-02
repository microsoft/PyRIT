# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, SuffixAppendConverter


async def test_suffix_append_basic():
    converter = SuffixAppendConverter(suffix="!!!")
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "hello !!!"
    assert result.output_type == "text"


async def test_suffix_append_long_suffix():
    converter = SuffixAppendConverter(suffix="please respond")
    result = await converter.convert_async(prompt="test", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "test please respond"
    assert result.output_type == "text"


async def test_suffix_append_empty_prompt():
    converter = SuffixAppendConverter(suffix="end")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == " end"
    assert result.output_type == "text"


def test_suffix_append_empty_suffix_raises():
    with pytest.raises(ValueError):
        SuffixAppendConverter(suffix="")


async def test_suffix_append_input_not_supported():
    converter = SuffixAppendConverter(suffix="end")
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
