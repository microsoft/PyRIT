# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, VariationSelectorSmugglerConverter


async def test_variation_selector_encode_basic():
    converter = VariationSelectorSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) > 0


async def test_variation_selector_decode_roundtrip():
    encoder = VariationSelectorSmugglerConverter(action="encode")
    encoded = await encoder.convert_async(prompt="test", input_type="text")

    decoder = VariationSelectorSmugglerConverter(action="decode")
    decoded = await decoder.convert_async(prompt=encoded.output_text, input_type="text")
    assert decoded.output_text == "test"


async def test_variation_selector_no_embed():
    converter = VariationSelectorSmugglerConverter(action="encode", embed_in_base=False)
    result = await converter.convert_async(prompt="a", input_type="text")
    base_char = converter.utf8_base_char
    # With embed_in_base=False, a space separator is inserted after the base char
    assert result.output_text.startswith(base_char + " ")


async def test_variation_selector_empty():
    converter = VariationSelectorSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="", input_type="text")
    # Empty input still produces base char prefix
    assert result.output_text == converter.utf8_base_char


def test_variation_selector_invalid_action():
    with pytest.raises(ValueError):
        VariationSelectorSmugglerConverter(action="invalid")


@pytest.mark.parametrize("base_char", ["", "ab", "😊x"])
def test_variation_selector_invalid_base_char(base_char):
    with pytest.raises(ValueError, match="base_char_utf8 must be exactly one character"):
        VariationSelectorSmugglerConverter(base_char_utf8=base_char)


async def test_variation_selector_custom_base_char_roundtrip():
    encoder = VariationSelectorSmugglerConverter(action="encode", base_char_utf8="A")
    encoded = await encoder.convert_async(prompt="test", input_type="text")

    decoder = VariationSelectorSmugglerConverter(action="decode", base_char_utf8="A")
    decoded = await decoder.convert_async(prompt=encoded.output_text, input_type="text")

    assert encoded.output_text.startswith("A")
    assert decoded.output_text == "test"


async def test_variation_selector_input_not_supported():
    converter = VariationSelectorSmugglerConverter(action="encode")
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")
