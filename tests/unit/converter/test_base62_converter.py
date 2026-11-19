# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import Base62Converter, ConverterResult

_BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _decode(encoded: str) -> bytes:
    """Reference Base62 -> bytes decoder used to verify round-tripping."""
    if encoded == "":
        return b""
    leading_zeros = 0
    for char in encoded:
        if char == _BASE62_ALPHABET[0]:
            leading_zeros += 1
        else:
            break
    number = 0
    for char in encoded[leading_zeros:]:
        number = number * 62 + _BASE62_ALPHABET.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * leading_zeros + body


@pytest.mark.parametrize(
    "plaintext, expected",
    [
        ("hello", "7tQLFHz"),
        ("hi", "6x7"),
        ("A", "13"),
        ("Hello, World!", "1wJfrzvdbtXUOlUjUf"),
    ],
)
async def test_base62_converter_known_vectors(plaintext, expected):
    converter = Base62Converter()
    result = await converter.convert_async(prompt=plaintext, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == expected
    assert result.output_type == "text"


async def test_base62_converter_empty():
    converter = Base62Converter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert result.output_text == ""


async def test_base62_converter_only_alphanumeric_chars():
    converter = Base62Converter()
    result = await converter.convert_async(prompt="The quick brown fox 123!@#", input_type="text")
    assert all(char in _BASE62_ALPHABET for char in result.output_text)


@pytest.mark.parametrize(
    "plaintext",
    [
        "hello",
        "A",
        "Hello, World!",
        "unicode: éà你好 \U0001f600",
        "\x00leading null",
        "trailing null\x00",
    ],
)
async def test_base62_converter_round_trip(plaintext):
    converter = Base62Converter()
    result = await converter.convert_async(prompt=plaintext, input_type="text")
    assert _decode(result.output_text).decode("utf-8") == plaintext


async def test_base62_converter_leading_null_bytes_preserved():
    # int.from_bytes drops leading zero bytes; the converter re-adds them as leading "0"s.
    converter = Base62Converter()
    # "\x00\x00A" -> two leading zeros ("00") + encoding of 0x41 ("13").
    result = await converter.convert_async(prompt="\x00\x00A", input_type="text")
    assert result.output_text == "0013"
    assert _decode(result.output_text) == b"\x00\x00A"


async def test_base62_converter_input_not_supported():
    converter = Base62Converter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


def test_base62_converter_input_supported():
    converter = Base62Converter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_base62_converter_output_supported():
    converter = Base62Converter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
