# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, SneakyBitsSmugglerConverter


async def test_sneaky_bits_encode_produces_invisible():
    converter = SneakyBitsSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    valid_chars = {converter.zero_char, converter.one_char}
    assert all(ch in valid_chars for ch in result.output_text)


async def test_sneaky_bits_decode_roundtrip():
    encoder = SneakyBitsSmugglerConverter(action="encode")
    encoded = await encoder.convert_async(prompt="test message", input_type="text")

    decoder = SneakyBitsSmugglerConverter(action="decode")
    decoded = await decoder.convert_async(prompt=encoded.output_text, input_type="text")
    assert decoded.output_text == "test message"


async def test_sneaky_bits_custom_chars():
    converter = SneakyBitsSmugglerConverter(action="encode", zero_char="0", one_char="1")
    result = await converter.convert_async(prompt="A", input_type="text")
    assert all(ch in {"0", "1"} for ch in result.output_text)
    assert len(result.output_text) == 8  # 1 ASCII byte = 8 bits


async def test_sneaky_bits_empty():
    converter = SneakyBitsSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="", input_type="text")
    assert result.output_text == ""


def test_sneaky_bits_invalid_action():
    with pytest.raises(ValueError):
        SneakyBitsSmugglerConverter(action="invalid")


async def test_sneaky_bits_input_not_supported():
    converter = SneakyBitsSmugglerConverter(action="encode")
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")
