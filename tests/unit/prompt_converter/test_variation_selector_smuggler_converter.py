# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ConverterResult, VariationSelectorSmugglerConverter


@pytest.mark.asyncio
async def test_variation_selector_encode_basic():
    converter = VariationSelectorSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) > 0


@pytest.mark.asyncio
async def test_variation_selector_decode_roundtrip():
    encoder = VariationSelectorSmugglerConverter(action="encode")
    encoded = await encoder.convert_async(prompt="test", input_type="text")

    decoder = VariationSelectorSmugglerConverter(action="decode")
    decoded = await decoder.convert_async(prompt=encoded.output_text, input_type="text")
    assert decoded.output_text == "test"


@pytest.mark.asyncio
async def test_variation_selector_no_embed():
    converter = VariationSelectorSmugglerConverter(action="encode", embed_in_base=False)
    result = await converter.convert_async(prompt="a", input_type="text")
    base_char = converter.utf8_base_char
    # With embed_in_base=False, a space separator is inserted after the base char
    assert result.output_text.startswith(base_char + " ")


@pytest.mark.asyncio
async def test_variation_selector_empty():
    converter = VariationSelectorSmugglerConverter(action="encode")
    result = await converter.convert_async(prompt="", input_type="text")
    # Empty input still produces base char prefix
    assert result.output_text == converter.utf8_base_char


def test_variation_selector_invalid_action():
    with pytest.raises(ValueError):
        VariationSelectorSmugglerConverter(action="invalid")


@pytest.mark.asyncio
async def test_variation_selector_input_not_supported():
    converter = VariationSelectorSmugglerConverter(action="encode")
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")
