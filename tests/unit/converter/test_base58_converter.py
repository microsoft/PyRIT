# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import Base58Converter, ConverterResult


async def test_base58_converter_default_known_vector():
    # Well-known Base58 (Bitcoin alphabet) vector.
    converter = Base58Converter()
    result = await converter.convert_async(prompt="hello world", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "StV1DL6CwTryKyV"
    assert result.output_type == "text"


async def test_base58_converter_known_vector_with_punctuation():
    converter = Base58Converter()
    result = await converter.convert_async(prompt="Hello World!", input_type="text")
    assert result.output_text == "2NEpo7TZRRrLZSi2U"


async def test_base58_converter_short_inputs():
    converter = Base58Converter()
    assert (await converter.convert_async(prompt="hello")).output_text == "Cn8eVZg"
    assert (await converter.convert_async(prompt="a")).output_text == "2g"
    assert (await converter.convert_async(prompt=" ")).output_text == "Z"


async def test_base58_converter_empty():
    converter = Base58Converter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_base58_converter_leading_null_bytes_become_ones():
    # A NUL byte encodes as the first alphabet character ("1"), not folded away.
    converter = Base58Converter()
    # "\x00" -> "1"; this exercises the leading-zero-byte branch.
    result = await converter.convert_async(prompt="\x00")
    assert result.output_text == "1"


async def test_base58_converter_flickr_alphabet():
    converter = Base58Converter(alphabet="flickr")
    result = await converter.convert_async(prompt="hello", input_type="text")
    # Same integer as the Bitcoin "Cn8eVZg" but mapped through the Flickr ordering.
    assert result.output_text == "cM8DuyF"


async def test_base58_converter_only_uses_alphabet_characters():
    converter = Base58Converter()
    alphabet = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
    result = await converter.convert_async(prompt="The quick brown fox jumps over the lazy dog.")
    assert set(result.output_text) <= alphabet
    # The ambiguous characters must never appear in the Bitcoin alphabet output.
    assert not (set(result.output_text) & set("0OIl"))


def test_base58_converter_rejects_unknown_alphabet():
    with pytest.raises(ValueError):
        Base58Converter(alphabet="base64")  # type: ignore[arg-type]


async def test_base58_converter_input_not_supported():
    converter = Base58Converter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


def test_base58_converter_input_supported():
    converter = Base58Converter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_base58_converter_output_supported():
    converter = Base58Converter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False


def test_base58_converter_identifier_includes_alphabet():
    identifier = Base58Converter(alphabet="flickr").get_identifier()
    assert identifier.params["alphabet"] == "flickr"
