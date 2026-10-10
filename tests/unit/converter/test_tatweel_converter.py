# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, TatweelConverter

TATWEEL = chr(0x0640)
ALEF = chr(0x0627)
BEH = chr(0x0628)


def test_input_supported():
    converter = TatweelConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image") is False


async def test_inserts_tatweel_between_adjacent_arabic_letters():
    result = await TatweelConverter().convert_async(prompt=ALEF + BEH, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == ALEF + TATWEEL + BEH


async def test_inserts_tatweel_between_each_adjacent_pair():
    # Three consecutive Arabic letters yield two insertion points, not one
    result = await TatweelConverter().convert_async(prompt=ALEF + BEH + ALEF)
    assert result.output_text == ALEF + TATWEEL + BEH + TATWEEL + ALEF


async def test_tatweel_count_controls_padding_length():
    result = await TatweelConverter(tatweel_count=3).convert_async(prompt=ALEF + BEH)
    assert result.output_text == ALEF + (TATWEEL * 3) + BEH


async def test_not_inserted_across_non_arabic_boundary():
    # A space between the two Arabic letters breaks adjacency, so no tatweel is added
    result = await TatweelConverter().convert_async(prompt=ALEF + " " + BEH)
    assert result.output_text == ALEF + " " + BEH


async def test_leaves_non_arabic_unchanged():
    result = await TatweelConverter().convert_async(prompt="abc")
    assert result.output_text == "abc"


async def test_empty_prompt_returns_empty():
    result = await TatweelConverter().convert_async(prompt="")
    assert result.output_text == ""


async def test_conversion_is_deterministic():
    converter = TatweelConverter()
    prompt = ALEF + BEH + ALEF
    first = await converter.convert_async(prompt=prompt)
    second = await converter.convert_async(prompt=prompt)
    assert first.output_text == second.output_text


@pytest.mark.parametrize("count", [0, -1])
def test_invalid_tatweel_count_raises(count):
    with pytest.raises(ValueError):
        TatweelConverter(tatweel_count=count)


async def test_input_type_not_supported_raises():
    with pytest.raises(ValueError):
        await TatweelConverter().convert_async(prompt=ALEF + BEH, input_type="image")
