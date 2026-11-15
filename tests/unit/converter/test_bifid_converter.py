# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import BifidConverter, ConverterResult

# Canonical keyed Polybius square used in the Wikipedia "Bifid cipher" worked example.
# Rows/cols (1-indexed in the article):
#   B G W K Z / Q P N D S / I O A X E / F C L U M / T H Y V R
WIKIPEDIA_KEY = "BGWKZQPNDSIOAXEFCLUMTHYVR"


async def test_bifid_converter_wikipedia_known_answer():
    # Wikipedia worked example: FLEEATONCE -> UAEOLWRINS (whole-message period).
    converter = BifidConverter(key=WIKIPEDIA_KEY)
    result = await converter.convert_async(prompt="FLEEATONCE", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "UAEOLWRINS"
    assert result.output_type == "text"


async def test_bifid_converter_default_square_known_answer():
    # Plain-alphabet square (no J):  A B C D E / F G H I K / L M N O P / Q R S T U / V W X Y Z
    # HELLO -> coords H(1,2) E(0,4) L(2,0) L(2,0) O(2,3)
    #   rows = 1 0 2 2 2, cols = 2 4 0 0 3  ->  1 0 2 2 2 2 4 0 0 3
    #   pairs (1,0)(2,2)(2,2)(4,0)(0,3) -> F N N V D
    converter = BifidConverter()
    result = await converter.convert_async(prompt="HELLO", input_type="text")
    assert result.output_text == "FNNVD"


async def test_bifid_converter_period_blocks():
    # Same key, period 5: FLEEA | TONCE encoded independently -> UAIEY | YDINS.
    converter = BifidConverter(key=WIKIPEDIA_KEY, period=5)
    result = await converter.convert_async(prompt="FLEEATONCE", input_type="text")
    assert result.output_text == "UAIEYYDINS"


async def test_bifid_converter_period_zero_is_whole_message():
    whole = BifidConverter(key=WIKIPEDIA_KEY, period=0)
    big_period = BifidConverter(key=WIKIPEDIA_KEY, period=100)
    result_whole = await whole.convert_async(prompt="FLEEATONCE", input_type="text")
    result_big = await big_period.convert_async(prompt="FLEEATONCE", input_type="text")
    # A period >= message length behaves like the whole-message default.
    assert result_whole.output_text == result_big.output_text == "UAEOLWRINS"


async def test_bifid_converter_strips_non_letters_and_uppercases():
    # Spaces, digits and punctuation are dropped; lowercase is uppercased first.
    plain = BifidConverter(key=WIKIPEDIA_KEY)
    spaced = await plain.convert_async(prompt="flee at once! (123)", input_type="text")
    assert spaced.output_text == "UAEOLWRINS"


async def test_bifid_converter_merges_j_into_i():
    # J is folded to I, so "JIM" encodes identically to "IIM".
    converter = BifidConverter()
    with_j = await converter.convert_async(prompt="JIM", input_type="text")
    with_i = await converter.convert_async(prompt="IIM", input_type="text")
    assert with_j.output_text == with_i.output_text


async def test_bifid_converter_key_ignores_non_letters_and_duplicates():
    # A key with punctuation/duplicates/J yields the same square as its cleaned form.
    noisy = BifidConverter(key="b-g w!k z q p n d s i o a x e f c l u m t h y v r")
    clean = BifidConverter(key=WIKIPEDIA_KEY)
    result_noisy = await noisy.convert_async(prompt="FLEEATONCE", input_type="text")
    result_clean = await clean.convert_async(prompt="FLEEATONCE", input_type="text")
    assert result_noisy.output_text == result_clean.output_text == "UAEOLWRINS"


async def test_bifid_converter_empty_and_non_letter_only_input():
    converter = BifidConverter()
    assert (await converter.convert_async(prompt="", input_type="text")).output_text == ""
    assert (await converter.convert_async(prompt="123 !?", input_type="text")).output_text == ""


async def test_bifid_converter_letter_free_key_equals_default_square():
    # A key with no letters leaves nothing to seed the square, so it collapses to the
    # default plain-alphabet square (identical to passing no key at all).
    default = BifidConverter()
    letter_free = BifidConverter(key="123 !?-")
    result_default = await default.convert_async(prompt="HELLO", input_type="text")
    result_letter_free = await letter_free.convert_async(prompt="HELLO", input_type="text")
    assert result_letter_free.output_text == result_default.output_text == "FNNVD"


async def test_bifid_converter_single_letter_roundtrip_identity():
    # A single-letter message maps to itself: coords (r,c) fractionate back to (r,c).
    converter = BifidConverter(key=WIKIPEDIA_KEY)
    result = await converter.convert_async(prompt="A", input_type="text")
    assert result.output_text == "A"


async def test_bifid_converter_negative_period_raises():
    with pytest.raises(ValueError):
        BifidConverter(period=-1)


async def test_bifid_converter_rejects_non_text_input():
    converter = BifidConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


async def test_bifid_converter_identifier_includes_params():
    converter = BifidConverter(key="secret", period=7)
    identifier = converter.get_identifier()
    assert identifier.params["key"] == "secret"
    assert identifier.params["period"] == 7
