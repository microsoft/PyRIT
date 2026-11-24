# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ArabiziConverter, ConverterResult

# Arabic letters (built from code points to keep this file pure ASCII).
ALEF = chr(0x0627)
BEH = chr(0x0628)
HAH = chr(0x062D)  # -> 7
KHAH = chr(0x062E)  # -> 5
REH = chr(0x0631)
SHEEN = chr(0x0634)  # -> sh
AIN = chr(0x0639)  # -> 3
QAF = chr(0x0642)  # -> 8
MEEM = chr(0x0645)
ALEF_MADDA = chr(0x0622)  # -> 2a
FATHA = chr(0x064E)  # short-vowel diacritic, dropped
TATWEEL = chr(0x0640)  # connector, dropped
ARABIC_COMMA = chr(0x060C)
ARABIC_SEMICOLON = chr(0x061B)
ARABIC_QUESTION_MARK = chr(0x061F)


def test_input_supported():
    converter = ArabiziConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image") is False


async def test_transliterates_word():
    # marhaba: MEEM REH HAH BEH ALEF -> m r 7 b a
    result = await ArabiziConverter().convert_async(prompt=MEEM + REH + HAH + BEH + ALEF, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "mr7ba"


async def test_number_letters():
    result = await ArabiziConverter().convert_async(prompt=HAH + KHAH + AIN + QAF)
    assert result.output_text == "753" + "8"


async def test_multi_character_mappings():
    result = await ArabiziConverter().convert_async(prompt=SHEEN + ALEF_MADDA)
    assert result.output_text == "sh2a"


async def test_diacritics_and_tatweel_are_dropped():
    # BEH + FATHA -> "b" (diacritic dropped); BEH + TATWEEL + BEH -> "bb"
    assert (await ArabiziConverter().convert_async(prompt=BEH + FATHA)).output_text == "b"
    assert (await ArabiziConverter().convert_async(prompt=BEH + TATWEEL + BEH)).output_text == "bb"


@pytest.mark.parametrize(
    ("arabic_punctuation", "latin_punctuation"),
    [(ARABIC_COMMA, ","), (ARABIC_SEMICOLON, ";"), (ARABIC_QUESTION_MARK, "?")],
)
async def test_arabic_punctuation_becomes_latin(arabic_punctuation, latin_punctuation):
    result = await ArabiziConverter().convert_async(prompt=BEH + arabic_punctuation)
    assert result.output_text == "b" + latin_punctuation


async def test_output_has_no_arabic_punctuation_left():
    # "kayf halak? ana bikhayr, shukran; marhaba" written with Arabic punctuation
    prompt = (
        "\u0643\u064a\u0641 \u062d\u0627\u0644\u0643\u061f "
        "\u0623\u0646\u0627 \u0628\u062e\u064a\u0631\u060c "
        "\u0634\u0643\u0631\u0627\u061b \u0645\u0631\u062d\u0628\u0627"
    )
    result = await ArabiziConverter().convert_async(prompt=prompt)
    assert result.output_text == "kyf 7alk? ana b5yr, shkra; mr7ba"


async def test_leaves_non_arabic_unchanged():
    result = await ArabiziConverter().convert_async(prompt="hello 123!")
    assert result.output_text == "hello 123!"


async def test_mixed_text():
    result = await ArabiziConverter().convert_async(prompt="ok " + BEH)
    assert result.output_text == "ok b"


async def test_empty_prompt_returns_empty():
    result = await ArabiziConverter().convert_async(prompt="")
    assert result.output_text == ""


async def test_conversion_is_deterministic():
    converter = ArabiziConverter()
    prompt = MEEM + REH + HAH + BEH + ALEF
    first = await converter.convert_async(prompt=prompt)
    second = await converter.convert_async(prompt=prompt)
    assert first.output_text == second.output_text


async def test_input_type_not_supported_raises():
    with pytest.raises(ValueError):
        await ArabiziConverter().convert_async(prompt=BEH, input_type="image")
