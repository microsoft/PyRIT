# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import unicodedata

import pytest

from pyrit.converter import (
    ConverterResult,
    MathematicalAlphanumericConverter,
    WordIndexSelectionStrategy,
)
from pyrit.converter.mathematical_alphanumeric_converter import _STYLE_BASES


async def test_bold_maps_letters_and_digits_async() -> None:
    result = await MathematicalAlphanumericConverter(style="bold").convert_async(prompt="Hello 123", input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # 𝐇𝐞𝐥𝐥𝐨 𝟏𝟐𝟑
    assert result.output_text == "\U0001d407\U0001d41e\U0001d425\U0001d425\U0001d428 \U0001d7cf\U0001d7d0\U0001d7d1"


async def test_default_style_is_bold_async() -> None:
    default_result = await MathematicalAlphanumericConverter().convert_async(prompt="Ab9", input_type="text")
    bold_result = await MathematicalAlphanumericConverter(style="bold").convert_async(prompt="Ab9", input_type="text")

    assert default_result.output_text == bold_result.output_text
    assert default_result.output_text == "\U0001d400\U0001d41b\U0001d7d7"


async def test_monospace_letters_and_digits_are_contiguous_async() -> None:
    result = await MathematicalAlphanumericConverter(style="monospace").convert_async(prompt="Az0", input_type="text")

    # A -> U+1D670, z -> U+1D68A + 25 = U+1D6A3, 0 -> U+1D7F6
    assert result.output_text == "\U0001d670\U0001d6a3\U0001d7f6"


async def test_italic_h_uses_letterlike_exception_async() -> None:
    result = await MathematicalAlphanumericConverter(style="italic").convert_async(prompt="h", input_type="text")

    # Reserved code point U+1D455 is a hole; the glyph is PLANCK CONSTANT (U+210E).
    assert result.output_text == "ℎ"


async def test_script_capital_exceptions_async() -> None:
    result = await MathematicalAlphanumericConverter(style="script").convert_async(prompt="HILR", input_type="text")

    # Script capitals H, I, L, R from the Letterlike Symbols block.
    assert result.output_text == "ℋℐℒℛ"


async def test_script_small_exceptions_async() -> None:
    result = await MathematicalAlphanumericConverter(style="script").convert_async(prompt="ego", input_type="text")

    # Script small e, g, o from the Letterlike Symbols block.
    assert result.output_text == "ℯℊℴ"


async def test_fraktur_capital_exceptions_async() -> None:
    result = await MathematicalAlphanumericConverter(style="fraktur").convert_async(prompt="CHIRZ", input_type="text")

    # Black-letter capitals C, H, I, R, Z from the Letterlike Symbols block.
    assert result.output_text == "ℭℌℑℜℨ"


async def test_double_struck_exceptions_and_digits_async() -> None:
    result = await MathematicalAlphanumericConverter(style="double_struck").convert_async(
        prompt="CHNPQRZ 0", input_type="text"
    )

    # Double-struck capitals C, H, N, P, Q, R, Z then double-struck 0 (U+1D7D8).
    assert result.output_text == "ℂℍℕℙℚℝℤ \U0001d7d8"


async def test_unmapped_characters_pass_through_async() -> None:
    # 'e' with acute accent (U+00E9) and CJK are non-ASCII and must pass through unchanged.
    result = await MathematicalAlphanumericConverter(style="bold").convert_async(prompt="café, 日", input_type="text")

    # c -> U+1D41C, a -> U+1D41A, f -> U+1D41F; accented e, comma, space and CJK unchanged.
    assert result.output_text == "\U0001d41c\U0001d41a\U0001d41fé, 日"


async def test_styles_without_digit_runs_leave_digits_unchanged_async() -> None:
    # italic defines no digit glyphs; digits must pass through untouched.
    result = await MathematicalAlphanumericConverter(style="italic").convert_async(prompt="a1", input_type="text")

    assert result.output_text == "\U0001d44e1"


@pytest.mark.parametrize("style", sorted(_STYLE_BASES))
async def test_every_style_emits_only_assigned_codepoints_async(style: str) -> None:
    # Converting the full alphabet (and digits) must never produce an unassigned code point,
    # which would mean a reserved "hole" in the block was not handled via an exception.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

    result = await MathematicalAlphanumericConverter(style=style).convert_async(prompt=alphabet, input_type="text")

    for char in result.output_text:
        # unicodedata.name raises ValueError for unassigned code points.
        assert unicodedata.name(char)


@pytest.mark.parametrize("style", sorted(_STYLE_BASES))
async def test_nfkc_normalization_round_trips_to_plaintext_async(style: str) -> None:
    # Every mapped glyph must carry a compatibility decomposition back to its plain ASCII
    # letter/digit, so NFKC-normalizing the converted alphabet restores the original input.
    # This pins the *represented* character of each glyph, not just that it is assigned: a one-off
    # base shift (for example, AZ -> Ba) is caught here even though the glyphs stay in the block.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

    result = await MathematicalAlphanumericConverter(style=style).convert_async(prompt=alphabet, input_type="text")

    assert unicodedata.normalize("NFKC", result.output_text) == alphabet


async def test_invalid_style_raises_async() -> None:
    with pytest.raises(ValueError):
        MathematicalAlphanumericConverter(style="not_a_style")


async def test_respects_word_selection_strategy_async() -> None:
    converter = MathematicalAlphanumericConverter(
        style="bold", word_selection_strategy=WordIndexSelectionStrategy(indices=[1])
    )

    result = await converter.convert_async(prompt="keep change keep", input_type="text")

    # Only the middle word ("change") is styled: c h a n g e -> bold glyphs.
    assert result.output_text == "keep \U0001d41c\U0001d421\U0001d41a\U0001d427\U0001d420\U0001d41e keep"


async def test_empty_prompt_returns_empty_text_async() -> None:
    result = await MathematicalAlphanumericConverter().convert_async(prompt="", input_type="text")

    assert result.output_text == ""


async def test_rejects_unsupported_input_type_async() -> None:
    with pytest.raises(ValueError):
        await MathematicalAlphanumericConverter().convert_async(prompt="hello", input_type="image_path")


def test_identifier_includes_style_and_is_stable() -> None:
    bold_first = MathematicalAlphanumericConverter(style="bold").get_identifier()
    bold_second = MathematicalAlphanumericConverter(style="bold").get_identifier()
    fraktur = MathematicalAlphanumericConverter(style="fraktur").get_identifier()

    assert bold_first.class_name == "MathematicalAlphanumericConverter"
    assert bold_first.hash == bold_second.hash
    # Different styles must produce distinct identifiers.
    assert bold_first.hash != fraktur.hash
