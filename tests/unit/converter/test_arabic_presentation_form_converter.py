# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ArabicPresentationFormConverter, ConverterResult

ALEF = chr(0x0627)
BEH = chr(0x0628)
ALEF_ISOLATED = chr(0xFE8D)
BEH_ISOLATED = chr(0xFE8F)
ARABIC_INDIC_ZERO = chr(0x0660)  # Category Nd, no isolated form
ARABIC_COMMA = chr(0x060C)  # Category Po, no isolated form


def test_input_supported():
    converter = ArabicPresentationFormConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image") is False


async def test_maps_arabic_letters_to_isolated_forms():
    result = await ArabicPresentationFormConverter().convert_async(prompt=ALEF + BEH, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == ALEF_ISOLATED + BEH_ISOLATED


async def test_leaves_non_arabic_unchanged():
    result = await ArabicPresentationFormConverter().convert_async(prompt="hello world")
    assert result.output_text == "hello world"


async def test_mixed_text():
    result = await ArabicPresentationFormConverter().convert_async(prompt="a" + ALEF + "b")
    assert result.output_text == "a" + ALEF_ISOLATED + "b"


async def test_arabic_non_letters_pass_through():
    # Arabic digits and punctuation have no isolated presentation form and must be untouched
    result = await ArabicPresentationFormConverter().convert_async(prompt=ARABIC_INDIC_ZERO + ARABIC_COMMA)
    assert result.output_text == ARABIC_INDIC_ZERO + ARABIC_COMMA


async def test_empty_prompt_returns_empty():
    result = await ArabicPresentationFormConverter().convert_async(prompt="")
    assert result.output_text == ""


async def test_conversion_is_deterministic():
    converter = ArabicPresentationFormConverter()
    first = await converter.convert_async(prompt=ALEF + BEH)
    second = await converter.convert_async(prompt=ALEF + BEH)
    assert first.output_text == second.output_text


async def test_input_type_not_supported_raises():
    with pytest.raises(ValueError):
        await ArabicPresentationFormConverter().convert_async(prompt=ALEF, input_type="image")
