# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import AsciiSmugglerConverter, ConverterResult


async def test_ascii_smuggler_encode_basic():
    converter = AsciiSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    for char in result.output_text:
        assert ord(char) > 0xE0000


async def test_ascii_smuggler_decode_roundtrip():
    encoder = AsciiSmugglerConverter(action="encode")
    encoded = await encoder.convert_async(prompt="hello", input_type="text")

    decoder = AsciiSmugglerConverter(action="decode")
    decoded = await decoder.convert_async(prompt=encoded.output_text, input_type="text")
    assert decoded.output_text == "hello"


async def test_ascii_smuggler_with_unicode_tags():
    converter = AsciiSmugglerConverter(action="encode", unicode_tags=True)
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert result.output_text.startswith(chr(0xE0001))
    assert result.output_text.endswith(chr(0xE007F))


async def test_ascii_smuggler_empty():
    converter = AsciiSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="", input_type="text")
    assert result.output_text == ""


def test_ascii_smuggler_invalid_action():
    with pytest.raises(ValueError):
        AsciiSmugglerConverter(action="invalid")


async def test_ascii_smuggler_input_not_supported():
    converter = AsciiSmugglerConverter(action="encode")
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")
