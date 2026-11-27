# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, RailFenceConverter


async def test_rail_fence_converter_known_vector():
    # Canonical Wikipedia Rail Fence example. The default is 3 rails, so this also proves the default.
    converter = RailFenceConverter()
    assert converter.rails == 3
    result = await converter.convert_async(prompt="WEAREDISCOVEREDFLEEATONCE", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "WECRLTEERDSOEEFEAOCAIVDEN"
    assert result.output_type == "text"


async def test_rail_fence_converter_two_rails():
    # Two rails split into even-index characters followed by odd-index characters.
    converter = RailFenceConverter(rails=2)
    result = await converter.convert_async(prompt="WEAREDISCOVERED", input_type="text")
    assert result.output_text == "WAEICVRDERDSOEE"


async def test_rail_fence_converter_preserves_all_characters():
    converter = RailFenceConverter(rails=3)
    prompt = "Hello, World! 123"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    # A transposition cipher is a permutation: length is preserved and it is a rearrangement.
    assert len(result.output_text) == len(prompt)
    assert sorted(result.output_text) == sorted(prompt)


async def test_rail_fence_converter_more_rails_than_characters():
    # With more rails than characters, no rail reaches the bottom, so output equals input.
    converter = RailFenceConverter(rails=10)
    result = await converter.convert_async(prompt="abc", input_type="text")
    assert result.output_text == "abc"


async def test_rail_fence_converter_empty():
    converter = RailFenceConverter(rails=3)
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


@pytest.mark.parametrize("rails", [1, 0, 2.5])
def test_rail_fence_converter_invalid_rails(rails):
    # Too few rails (1, 0) and non-integers (2.5) are all rejected in the constructor.
    with pytest.raises(ValueError, match="rail fence rails value invalid"):
        RailFenceConverter(rails=rails)


async def test_rail_fence_converter_decode_prompt_description_includes_rail_count():
    converter = RailFenceConverter(rails=4)
    assert "4 rails" in converter.decode_prompt_description


def test_rail_fence_converter_identifier_includes_rails():
    converter = RailFenceConverter(rails=4)
    identifier = converter.get_identifier()
    assert identifier.params["rails"] == 4


async def test_rail_fence_converter_input_not_supported():
    converter = RailFenceConverter(rails=3)
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
