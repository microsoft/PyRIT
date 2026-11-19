# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import Base32Converter, ConverterResult


# Standard Base32 test vectors from RFC 4648 section 10.
@pytest.mark.parametrize(
    "plaintext, expected",
    [
        ("", ""),
        ("f", "MY======"),
        ("fo", "MZXQ===="),
        ("foo", "MZXW6==="),
        ("foob", "MZXW6YQ="),
        ("fooba", "MZXW6YTB"),
        ("foobar", "MZXW6YTBOI======"),
    ],
)
async def test_base32_converter_rfc4648_vectors(plaintext, expected):
    converter = Base32Converter()
    result = await converter.convert_async(prompt=plaintext, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == expected
    assert result.output_type == "text"


# Extended Hex Base32 test vectors from RFC 4648 section 10.
@pytest.mark.parametrize(
    "plaintext, expected",
    [
        ("", ""),
        ("f", "CO======"),
        ("fo", "CPNG===="),
        ("foo", "CPNMU==="),
        ("foob", "CPNMUOG="),
        ("fooba", "CPNMUOJ1"),
        ("foobar", "CPNMUOJ1E8======"),
    ],
)
async def test_base32_converter_b32hex_vectors(plaintext, expected):
    converter = Base32Converter(variant="b32hex")
    result = await converter.convert_async(prompt=plaintext, input_type="text")
    assert result.output_text == expected
    assert result.output_type == "text"


async def test_base32_converter_default_variant_is_b32():
    converter = Base32Converter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert result.output_text == "NBSWY3DP"


async def test_base32_converter_utf8_multibyte():
    # "é" is U+00E9 -> UTF-8 bytes 0xC3 0xA9.
    converter = Base32Converter()
    result = await converter.convert_async(prompt="é", input_type="text")
    assert result.output_text == "YOUQ===="


async def test_base32_converter_input_not_supported():
    converter = Base32Converter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


def test_base32_converter_input_supported():
    converter = Base32Converter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_base32_converter_output_supported():
    converter = Base32Converter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False


def test_base32_converter_identifier_distinguishes_variant():
    # Different variants are behaviorally distinct and must hash differently.
    b32 = Base32Converter(variant="b32").get_identifier()
    b32hex = Base32Converter(variant="b32hex").get_identifier()
    assert b32.hash != b32hex.hash
    # Same variant is stable.
    assert b32.hash == Base32Converter(variant="b32").get_identifier().hash
