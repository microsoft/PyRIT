# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import Base2048Converter, ConverterResult


@pytest.mark.asyncio
async def test_base2048_converter_basic():
    converter = Base2048Converter()
    prompt = "Hello"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text is not None
    assert result.output_type == "text"
    assert len(result.output_text) > 0


@pytest.mark.asyncio
async def test_base2048_converter_unicode():
    converter = Base2048Converter()
    prompt = "Hello, 世界! 🌍"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text is not None
    assert result.output_type == "text"


@pytest.mark.asyncio
async def test_base2048_converter_input_supported():
    converter = Base2048Converter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


@pytest.mark.asyncio
async def test_base2048_converter_output_supported():
    converter = Base2048Converter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False


@pytest.mark.asyncio
async def test_base2048_converter_invalid_input_type():
    converter = Base2048Converter()
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")
