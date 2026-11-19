# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, RegionalIndicatorConverter

# U+1F1E6 REGIONAL INDICATOR SYMBOL LETTER A.
REGIONAL_INDICATOR_A = 0x1F1E6
# Zero-width non-joiner inserted between adjacent tiles.
ZWNJ = "‌"


def _tile(letter: str) -> str:
    """Return the regional indicator tile for a single ASCII letter a-z/A-Z."""
    return chr(REGIONAL_INDICATOR_A + ord(letter.lower()) - ord("a"))


async def test_regional_indicator_simple_letters():
    """Each letter maps to its regional indicator, separated by a zero-width non-joiner."""
    converter = RegionalIndicatorConverter()

    result = await converter.convert_async(prompt="abc", input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == _tile("a") + ZWNJ + _tile("b") + ZWNJ + _tile("c")


async def test_regional_indicator_case_insensitive():
    """Uppercase and lowercase letters map to the same tiles."""
    converter = RegionalIndicatorConverter()

    lower = await converter.convert_async(prompt="hello", input_type="text")
    upper = await converter.convert_async(prompt="HELLO", input_type="text")

    assert lower.output_text == upper.output_text


async def test_regional_indicator_known_code_points():
    """A -> U+1F1E6 and Z -> U+1F1FF (block boundaries)."""
    converter = RegionalIndicatorConverter()

    result = await converter.convert_async(prompt="AZ", input_type="text")

    assert result.output_text == chr(0x1F1E6) + ZWNJ + chr(0x1F1FF)


async def test_regional_indicator_non_letters_unchanged():
    """Digits, punctuation, and spaces pass through unchanged and break the tile run."""
    converter = RegionalIndicatorConverter()

    result = await converter.convert_async(prompt="a1 b", input_type="text")

    # "a" then "1", space pass through, then "b". No separator is inserted because
    # the letters are not adjacent.
    assert result.output_text == _tile("a") + "1 " + _tile("b")


async def test_regional_indicator_separator_only_between_adjacent_tiles():
    """The non-joiner appears only between consecutive tiles, not around non-letters."""
    converter = RegionalIndicatorConverter()

    result = await converter.convert_async(prompt="ab cd", input_type="text")

    expected = _tile("a") + ZWNJ + _tile("b") + " " + _tile("c") + ZWNJ + _tile("d")
    assert result.output_text == expected
    # The space-separated pair should not be joined.
    assert f"{_tile('b')} {_tile('c')}" in result.output_text


async def test_regional_indicator_single_letter_has_no_separator():
    """A single letter produces one tile with no separator."""
    converter = RegionalIndicatorConverter()

    result = await converter.convert_async(prompt="x", input_type="text")

    assert result.output_text == _tile("x")
    assert ZWNJ not in result.output_text


async def test_regional_indicator_empty_prompt():
    """An empty prompt yields empty output."""
    converter = RegionalIndicatorConverter()

    result = await converter.convert_async(prompt="", input_type="text")

    assert result.output_text == ""


async def test_regional_indicator_non_ascii_letters_pass_through():
    """Accented and non-Latin letters have no regional indicator and are left unchanged."""
    converter = RegionalIndicatorConverter()

    result = await converter.convert_async(prompt="café中", input_type="text")

    assert result.output_text == _tile("c") + ZWNJ + _tile("a") + ZWNJ + _tile("f") + "é中"


async def test_regional_indicator_input_type_not_supported():
    """Non-text input types raise ValueError."""
    converter = RegionalIndicatorConverter()

    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")


def test_regional_indicator_input_supported():
    """input_supported only accepts text."""
    converter = RegionalIndicatorConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_regional_indicator_output_supported():
    """output_supported only accepts text."""
    converter = RegionalIndicatorConverter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False
