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


async def test_url_converter_default_preserves_forward_slash_async() -> None:
    converter = UrlConverter()
    result = await converter.convert_async(prompt="https://example.com/a b")
    assert result.output_text == "https%3A//example.com/a%20b"


@pytest.mark.parametrize(
    ("safe", "prompt", "expected"),
    [
        ("", "../../etc/passwd", "..%2F..%2Fetc%2Fpasswd"),
        ("", "https://example.com/test", "https%3A%2F%2Fexample.com%2Ftest"),
        ("/:", "https://example.com/test", "https://example.com/test"),
        ("?=", "?query=1&foo=bar", "?query=1%26foo=bar"),
    ],
)
async def test_url_converter_custom_safe_async(*, safe: str, prompt: str, expected: str) -> None:
    converter = UrlConverter(safe=safe)
    result = await converter.convert_async(prompt=prompt)
    assert result.output_text == expected


@pytest.mark.parametrize("safe", ["/", "", "/:"])
def test_url_converter_identifier_and_reconstruction(safe: str) -> None:
    converter = UrlConverter(safe=safe)
    assert converter.get_identifier().params["safe"] == safe
    params = converter.get_reconstruction_parameters()
    assert params == {"safe": safe}
    assert UrlConverter(**params).get_identifier() == converter.get_identifier()


def test_url_converter_default_reconstruction() -> None:
    converter = UrlConverter()
    assert converter.get_identifier().params["safe"] == "/"
    assert converter.get_reconstruction_parameters() == {"safe": "/"}
