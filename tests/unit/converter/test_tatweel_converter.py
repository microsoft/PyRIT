# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, TatweelConverter

TATWEEL = chr(0x0640)
ALEF = chr(0x0627)
BEH = chr(0x0628)
TEH_MARBUTA = chr(0x0629)
DAL = chr(0x062F)
REH = chr(0x0631)
WAW = chr(0x0648)
HAMZA = chr(0x0621)
ALEF_WITH_HAMZA_ABOVE = chr(0x0623)


def test_input_supported():
    converter = TatweelConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image") is False


async def test_inserts_tatweel_between_adjacent_arabic_letters():
    result = await TatweelConverter().convert_async(prompt=BEH + ALEF, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == BEH + TATWEEL + ALEF


async def test_inserts_tatweel_between_each_adjacent_pair():
    # Three consecutive Arabic letters yield two insertion points, not one
    result = await TatweelConverter().convert_async(prompt=BEH + BEH + ALEF)
    assert result.output_text == BEH + TATWEEL + BEH + TATWEEL + ALEF


async def test_tatweel_count_controls_padding_length():
    result = await TatweelConverter(tatweel_count=3).convert_async(prompt=BEH + ALEF)
    assert result.output_text == BEH + (TATWEEL * 3) + ALEF


@pytest.mark.parametrize("letter", [ALEF, ALEF_WITH_HAMZA_ABOVE, DAL, REH, WAW, TEH_MARBUTA, HAMZA])
async def test_not_inserted_after_letter_that_does_not_join_the_next(letter):
    # Right-joining and non-joining letters never connect to the following letter, so a
    # tatweel after them would be a detached stroke rather than a lengthened connection
    result = await TatweelConverter().convert_async(prompt=letter + BEH)
    assert result.output_text == letter + BEH


async def test_not_inserted_before_hamza():
    # Hamza does not join the letter before it either
    result = await TatweelConverter().convert_async(prompt=BEH + HAMZA)
    assert result.output_text == BEH + HAMZA


async def test_inserted_only_at_joins_in_words():
    # "tell me how to cut down a tree": no tatweel after alef with hamza above or reh
    prompt = "\u0623\u062e\u0628\u0631\u0646\u064a \u0643\u064a\u0641 \u0623\u0642\u0637\u0639 \u0634\u062c\u0631\u0629"
    expected = (
        "\u0623\u062e-\u0628-\u0631\u0646-\u064a \u0643-\u064a-\u0641 "
        "\u0623\u0642-\u0637-\u0639 \u0634-\u062c-\u0631\u0629"
    ).replace("-", TATWEEL)
    result = await TatweelConverter().convert_async(prompt=prompt)
    assert result.output_text == expected


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
    prompt = BEH + BEH + ALEF
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
