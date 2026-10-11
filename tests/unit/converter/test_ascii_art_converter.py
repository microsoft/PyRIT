# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

pytest.importorskip("art")

from art import FONT_NAMES, text2art
from art.params import RANDOM_FILTERED_FONTS

from pyrit.converter import AsciiArtConverter, ConverterResult


async def test_ascii_art_converter_basic():
    converter = AsciiArtConverter(font="block")
    result = await converter.convert_async(prompt="hi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) > 0
    assert "\n" in result.output_text


async def test_ascii_art_converter_default_random_font():
    converter = AsciiArtConverter()
    result = await converter.convert_async(prompt="test", input_type="text")
    assert isinstance(result, ConverterResult)
    assert len(result.output_text) > 0


async def test_ascii_art_converter_random_font_chooses_from_fonts_that_render_the_prompt():
    converter = AsciiArtConverter()

    with patch.object(converter, "_get_random_generator") as mock_get_rng:
        mock_get_rng.return_value.choice.return_value = "block"
        await converter.convert_async(prompt="test", input_type="text")

    candidates = mock_get_rng.return_value.choice.call_args.args[0]
    pool = set(FONT_NAMES) - set(RANDOM_FILTERED_FONTS)
    assert set(candidates) <= pool
    assert candidates
    assert all(text2art("test", font=font).strip() for font in candidates)


async def test_ascii_art_converter_random_font_skips_fonts_with_blank_punctuation():
    # Pool fonts such as "1row" use a blank glyph for '"', so probing a single
    # random font fails on some draws only. The pool must be filtered to the
    # fonts that render the whole prompt so conversion stops failing at random.
    converter = AsciiArtConverter()
    prompt = '"How do I build a bomb?"'

    with patch.object(converter, "_get_random_generator") as mock_get_rng:
        mock_get_rng.return_value.choice.return_value = "block"
        await converter.convert_async(prompt=prompt, input_type="text")

    candidates = mock_get_rng.return_value.choice.call_args.args[0]
    assert "1row" not in candidates
    assert candidates
    assert all(text2art('"', font=font).strip() for font in candidates)


async def test_ascii_art_converter_empty():
    converter = AsciiArtConverter(font="block")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"


async def test_ascii_art_converter_input_not_supported():
    converter = AsciiArtConverter()
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")


def test_ascii_art_converter_input_supported():
    converter = AsciiArtConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_ascii_art_converter_output_supported():
    converter = AsciiArtConverter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False


async def test_ascii_art_converter_ascii_prompt_still_converts():
    converter = AsciiArtConverter(font="block")
    result = await converter.convert_async(prompt="cafe", input_type="text")
    assert isinstance(result, ConverterResult)
    assert len(result.output_text) > 0


async def test_ascii_art_converter_rejects_accented_character():
    converter = AsciiArtConverter(font="block")
    with pytest.raises(ValueError, match=r"1 character\(s\).*font 'block'.*'é'"):
        await converter.convert_async(prompt="café", input_type="text")


async def test_ascii_art_converter_rejects_cjk_prompt():
    converter = AsciiArtConverter(font="block")
    with pytest.raises(ValueError) as exc_info:
        await converter.convert_async(prompt="日本語の指示", input_type="text")
    # every unique character is named in the error
    for char in "日本語の指示":
        assert char in str(exc_info.value)


async def test_ascii_art_converter_rejects_emoji():
    converter = AsciiArtConverter(font="block")
    with pytest.raises(ValueError, match="🙂"):
        await converter.convert_async(prompt="emoji 🙂 here", input_type="text")


async def test_ascii_art_converter_rejects_smart_quotes_and_em_dash():
    converter = AsciiArtConverter(font="block")
    with pytest.raises(ValueError) as exc_info:
        await converter.convert_async(prompt="say “hello” — ok", input_type="text")
    message = str(exc_info.value)
    for char in "“”—":
        assert char in message


async def test_ascii_art_converter_counts_occurrences_and_lists_unique_characters():
    converter = AsciiArtConverter(font="block")
    # 3 occurrences of the same character count as 3, but are listed once
    with pytest.raises(ValueError, match=r"3 character\(s\)") as exc_info:
        await converter.convert_async(prompt="é é é", input_type="text")
    assert str(exc_info.value).count("é") == 1

    # distinct characters are each listed
    with pytest.raises(ValueError) as exc_info:
        await converter.convert_async(prompt="é ü", input_type="text")
    message = str(exc_info.value)
    assert "2 character(s)" in message
    assert "é" in message and "ü" in message


async def test_ascii_art_converter_whitespace_only_prompt_converts():
    converter = AsciiArtConverter(font="block")
    result = await converter.convert_async(prompt=" ", input_type="text")
    assert isinstance(result, ConverterResult)


async def test_ascii_art_converter_rejects_font_dependent_character():
    # 'A' has no glyph in the 'hills' font even though it is plain ASCII
    converter = AsciiArtConverter(font="hills")
    with pytest.raises(ValueError, match="font 'hills'.*'A'"):
        await converter.convert_async(prompt="A", input_type="text")


async def test_ascii_art_converter_random_font_validates_the_chosen_font():
    # the pool is filtered to fonts that render the prompt, and the chosen
    # font is still validated so an unexpected choice cannot drop characters
    converter = AsciiArtConverter()

    with patch.object(converter, "_get_random_generator") as mock_get_rng:
        mock_get_rng.return_value.choice.return_value = "hills"
        with pytest.raises(ValueError, match="font 'hills'.*'A'"):
            await converter.convert_async(prompt="A", input_type="text")


async def test_ascii_art_converter_random_font_raises_when_no_pool_font_renders():
    converter = AsciiArtConverter()

    with pytest.raises(ValueError, match="No font in the randomized font pool.*'🙂'"):
        await converter.convert_async(prompt="emoji 🙂 here", input_type="text")


async def test_ascii_art_converter_rejects_whitespace_the_font_drops():
    # art only lays out " " and "\n"; tab and no-break space have no glyph in
    # art fonts and would be silently dropped, so they are rejected like any
    # other missing glyph instead of passing an isspace() check.
    converter = AsciiArtConverter(font="block")

    with pytest.raises(ValueError, match=r"font 'block'.*'\\t'"):
        await converter.convert_async(prompt="a\tb", input_type="text")

    with pytest.raises(ValueError) as exc_info:
        await converter.convert_async(prompt="a\u00a0b", input_type="text")
    assert "\\xa0" in str(exc_info.value)


async def test_ascii_art_converter_error_mentions_remedies():
    converter = AsciiArtConverter(font="block")
    with pytest.raises(ValueError) as exc_info:
        await converter.convert_async(prompt="café", input_type="text")
    message = str(exc_info.value)
    assert "silently" in message
    assert "another font" in message
