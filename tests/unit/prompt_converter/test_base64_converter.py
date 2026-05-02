# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import Base64Converter, ConverterResult


async def test_base64_converter_default():
    converter = Base64Converter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "aGVsbG8="
    assert result.output_type == "text"


async def test_base64_converter_urlsafe():
    converter = Base64Converter(encoding_func="urlsafe_b64encode")
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "aGVsbG8="
    assert result.output_type == "text"


async def test_base64_converter_b16():
    converter = Base64Converter(encoding_func="b16encode")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "6869"
    assert result.output_type == "text"


async def test_base64_converter_empty():
    converter = Base64Converter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_base64_converter_input_not_supported():
    converter = Base64Converter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


def test_base64_converter_input_supported():
    converter = Base64Converter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_base64_converter_output_supported():
    converter = Base64Converter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
