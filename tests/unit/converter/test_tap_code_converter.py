# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, TapCodeConverter


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        # A is at (row 1, column 1).
        ("A", ". ."),
        # Standard tap-code worked example WATER: W=(5,2) A=(1,1) T=(4,4) E=(1,5) R=(4,2).
        ("WATER", "..... ..  . .  .... ....  . .....  .... .."),
        # Corner letters: A=(1,1), E=(1,5), V=(5,1), Z=(5,5).
        ("AEVZ", ". .  . .....  ..... .  ..... ....."),
        # C and K share (row 1, column 3), so both encode identically.
        ("C", ". ..."),
        ("K", ". ..."),
    ],
)
async def test_tap_code_converter_encodes_letters(prompt, expected):
    converter = TapCodeConverter()
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == expected


async def test_tap_code_converter_case_insensitive():
    converter = TapCodeConverter()
    lower = await converter.convert_async(prompt="water", input_type="text")
    upper = await converter.convert_async(prompt="WATER", input_type="text")
    assert lower.output_text == upper.output_text


async def test_tap_code_converter_preserves_non_letters():
    converter = TapCodeConverter()
    result = await converter.convert_async(prompt="A1!", input_type="text")
    # The digit and punctuation pass through verbatim, joined by the letter separator.
    assert result.output_text == ". .  1  !"


@pytest.mark.parametrize("char", ["ı", "ſ", "é", "ß"])
async def test_tap_code_converter_preserves_non_ascii(char):
    # Characters whose upper-case form is an ASCII letter (ı->I, ſ->S) must still pass through verbatim
    # rather than being silently re-encoded; é and ß have no ASCII upper-case letter at all.
    converter = TapCodeConverter()
    result = await converter.convert_async(prompt=char, input_type="text")
    assert result.output_text == char


async def test_tap_code_converter_empty():
    converter = TapCodeConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


def test_tap_code_converter_folds_k_to_c_for_decoding():
    # The grid has no K cell, so a faithful decode returns C wherever the seed had K. The scenario reads
    # this hook to mark the decoded text as acceptable; folding must preserve case and leave the rest intact.
    assert TapCodeConverter.to_ascii_equivalent(text="Kaffir") == "Caffir"
    assert TapCodeConverter.to_ascii_equivalent(text="KICK kick") == "CICC cicc"
    assert TapCodeConverter.to_ascii_equivalent(text="cat") == "cat"


async def test_tap_code_converter_input_not_supported():
    converter = TapCodeConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
