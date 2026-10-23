# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, UrlConverter


async def test_url_converter_basic():
    converter = UrlConverter()
    result = await converter.convert_async(prompt="hello world", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "hello%20world"
    assert result.output_type == "text"


async def test_url_converter_special_chars():
    converter = UrlConverter()
    result = await converter.convert_async(prompt="a&b=c", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "a%26b%3Dc"
    assert result.output_type == "text"


async def test_url_converter_already_safe():
    converter = UrlConverter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "hello"
    assert result.output_type == "text"


async def test_url_converter_empty():
    converter = UrlConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_url_converter_input_not_supported():
    converter = UrlConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


async def test_url_converter_default_encodes_forward_slash():
    converter = UrlConverter()
    result = await converter.convert_async(prompt="../../etc/passwd", input_type="text")
    assert isinstance(result, ConverterResult)
    assert "/" not in result.output_text
    assert result.output_text == "..%2F..%2Fetc%2Fpasswd"
    assert result.output_type == "text"

    ssrf_result = await converter.convert_async(prompt="https://attacker.com/steal", input_type="text")
    assert "/" not in ssrf_result.output_text
    assert ssrf_result.output_text == "https%3A%2F%2Fattacker.com%2Fsteal"


async def test_url_converter_custom_safe():
    converter_slash = UrlConverter(safe="/")
    result_slash = await converter_slash.convert_async(prompt="../../etc/passwd", input_type="text")
    assert result_slash.output_text == "../../etc/passwd"

    converter_url = UrlConverter(safe="/:")
    result_url = await converter_url.convert_async(prompt="https://example.com/test", input_type="text")
    assert result_url.output_text == "https://example.com/test"

    converter_query = UrlConverter(safe="?=")
    result_query = await converter_query.convert_async(prompt="?query=1&foo=bar", input_type="text")
    assert result_query.output_text == "?query=1%26foo=bar"


def test_url_converter_property_and_identifier():
    default_converter = UrlConverter()
    assert default_converter.safe == ""
    assert default_converter.get_identifier().params["safe"] == ""

    custom_converter = UrlConverter(safe="/")
    assert custom_converter.safe == "/"
    assert custom_converter.get_identifier().params["safe"] == "/"


def test_url_converter_invalid_safe_type():
    with pytest.raises(TypeError, match="safe must be a str"):
        UrlConverter(safe=123)  # type: ignore[arg-type]


def test_url_converter_reconstruction():
    default_converter = UrlConverter()
    assert default_converter.get_reconstruction_parameters() == {"safe": ""}

    custom_converter = UrlConverter(safe="/")
    assert custom_converter.get_reconstruction_parameters() == {"safe": "/"}
