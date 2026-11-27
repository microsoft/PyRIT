# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import Base32Converter, Base64Converter, ConverterResult


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


async def test_base32_converter_encodes_standard_alphabet():
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


async def test_base32_converter_matches_base64_b32encode_output():
    # Base32Converter shares the byte-encoding base with Base64Converter and must produce
    # identical bytes to Base64Converter(encoding_func="b32encode").
    base32 = await Base32Converter().convert_async(prompt="foobar", input_type="text")
    base64_b32 = await Base64Converter(encoding_func="b32encode").convert_async(prompt="foobar", input_type="text")
    assert base32.output_text == base64_b32.output_text


def test_base32_converter_identifier_is_stable_and_distinct_from_base64():
    # The named Base32Converter keeps its own identity even though its output matches
    # Base64Converter(encoding_func="b32encode").
    identifier = Base32Converter().get_identifier()
    assert identifier.hash == Base32Converter().get_identifier().hash
    assert identifier.hash != Base64Converter(encoding_func="b32encode").get_identifier().hash
