# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

pytest.importorskip("art")

import logging

from art import FONT_NAMES, text2art
from art.functions import get_font_dic
from art.params import RANDOM_FILTERED_FONTS
from art.utils import indirect_font
from wcwidth import wcswidth

from pyrit.converter import AsciiArtConverter, ConverterResult
from pyrit.converter.ascii_art_converter import (
    _ART_RANDOM_FONTS,
    _ART_RANDOM_SELECTORS,
    _fonts_that_render,
)

_LOGGER_NAME = "pyrit.converter.ascii_art_converter"


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


def test_art_random_font_pool_matches_art_random_filter():
    # the exclusion list is hardcoded, so it has to be re-derived whenever `art`
    # changes which fonts it filters out of its own random choice
    assert set(_ART_RANDOM_FONTS) == set(FONT_NAMES) - set(RANDOM_FILTERED_FONTS)


@pytest.mark.parametrize("selector", sorted(_ART_RANDOM_SELECTORS))
def test_art_random_selectors_resolve_into_the_mirrored_pool(selector):
    # these are intercepted instead of being left to art, which would redraw a
    # font on every text2art call, so art must still resolve them over the same pool
    assert {indirect_font(selector, "test") for _ in range(25)} <= set(_ART_RANDOM_FONTS)


@pytest.mark.parametrize("selector", sorted(_ART_RANDOM_SELECTORS))
async def test_ascii_art_converter_random_font_chooses_from_fonts_that_render_the_prompt(selector):
    converter = AsciiArtConverter(font=selector)

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


async def test_ascii_art_converter_passes_through_accented_character(caplog):
    converter = AsciiArtConverter(font="block")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await converter.convert_async(prompt="café", input_type="text")
    assert "é" in result.output_text
    assert "é" in caplog.text


async def test_ascii_art_converter_passthrough_keeps_rendering_art_around_the_passed_character():
    # the renderable run still has to be laid out as art: returning the prompt
    # verbatim would also satisfy a bare `"é" in output_text` assertion
    converter = AsciiArtConverter(font="block")
    result = await converter.convert_async(prompt="café", input_type="text")

    art_rows = text2art("caf", font="block").strip("\n").splitlines()
    output_rows = result.output_text.strip("\n").splitlines()

    assert len(output_rows) == len(art_rows)
    assert all(output_row.startswith(art_row) for art_row, output_row in zip(art_rows, output_rows, strict=True))
    # the one-row passed-through character is centered against the art block
    assert output_rows[len(output_rows) // 2].endswith("é")


async def test_ascii_art_converter_resolves_an_indirect_font_once_per_conversion(caplog):
    # art redraws a concrete font on every text2art call for selectors like "wizard",
    # so without a single up-front resolution each pass-through segment of a line
    # would be rendered in a different font
    converter = AsciiArtConverter(font="wizard")
    with patch("pyrit.converter.ascii_art_converter.indirect_font", return_value="block") as mock_resolve:
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            result = await converter.convert_async(prompt="café café", input_type="text")

    mock_resolve.assert_called_once_with("wizard", "café café")
    expected = await AsciiArtConverter(font="block").convert_async(prompt="café café", input_type="text")
    assert result.output_text == expected.output_text
    # the warning names the font actually rendered with, not the selector
    assert "'block'" in caplog.text


async def test_ascii_art_converter_passes_through_cjk_prompt(caplog):
    converter = AsciiArtConverter(font="block")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await converter.convert_async(prompt="日本語の指示", input_type="text")
    # every character survives instead of the prompt collapsing to an empty string
    for char in "日本語の指示":
        assert char in result.output_text
        assert char in caplog.text


async def test_ascii_art_converter_passes_through_emoji():
    converter = AsciiArtConverter(font="block")
    result = await converter.convert_async(prompt="emoji 🙂 here", input_type="text")
    assert "🙂" in result.output_text


async def test_ascii_art_converter_passes_through_smart_quotes_and_em_dash(caplog):
    converter = AsciiArtConverter(font="block")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await converter.convert_async(prompt="say “hello” — ok", input_type="text")
    for char in "“”—":
        assert char in result.output_text
        assert char in caplog.text


async def test_ascii_art_converter_warning_lists_unique_characters(caplog):
    converter = AsciiArtConverter(font="block")
    # repeated characters are listed once
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await converter.convert_async(prompt="é é é", input_type="text")
    assert "1 character(s)" in caplog.text
    assert caplog.text.count("é") == 1

    caplog.clear()
    # distinct characters are each listed
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await converter.convert_async(prompt="é ü", input_type="text")
    assert "2 character(s)" in caplog.text
    assert "é" in caplog.text and "ü" in caplog.text


async def test_ascii_art_converter_whitespace_only_prompt_converts():
    converter = AsciiArtConverter(font="block")
    result = await converter.convert_async(prompt=" ", input_type="text")
    assert isinstance(result, ConverterResult)


async def test_ascii_art_converter_passes_through_font_dependent_character(caplog):
    # 'A' has no glyph in the 'hills' font even though it is plain ASCII
    converter = AsciiArtConverter(font="hills")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await converter.convert_async(prompt="A", input_type="text")
    assert "A" in result.output_text
    assert "'hills'" in caplog.text


async def test_ascii_art_converter_random_font_passes_through_for_the_chosen_font(caplog):
    # the pool is filtered to fonts that render the prompt, and the chosen
    # font is still checked so an unexpected choice cannot drop characters
    converter = AsciiArtConverter()

    with patch.object(converter, "_get_random_generator") as mock_get_rng:
        mock_get_rng.return_value.choice.return_value = "hills"
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            result = await converter.convert_async(prompt="A", input_type="text")

    assert "A" in result.output_text
    assert "'hills'" in caplog.text


async def test_ascii_art_converter_random_font_falls_back_when_no_pool_font_renders(caplog):
    converter = AsciiArtConverter()

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await converter.convert_async(prompt="emoji 🙂 here", input_type="text")

    # no font in the pool renders the emoji, so it is passed through rather than
    # failing the conversion outright
    assert "🙂" in result.output_text
    assert "🙂" in caplog.text


async def test_ascii_art_converter_passes_through_whitespace_the_font_drops():
    # " ", "\n" and "\r" are treated as layout; tab and no-break space have no
    # glyph in art fonts and would be silently dropped, so they go through the
    # same pass-through path instead of an isspace() exemption.
    converter = AsciiArtConverter(font="block")

    result = await converter.convert_async(prompt="a\tb", input_type="text")
    assert "\t" in result.output_text

    result = await converter.convert_async(prompt="a\u00a0b", input_type="text")
    assert "\u00a0" in result.output_text


async def test_ascii_art_converter_treats_carriage_return_as_a_line_ending(caplog):
    # \r is CRLF line-ending encoding rather than prompt content; passing it through
    # would splice a control character into the art block and warn on every prompt
    # authored on Windows, so CRLF prompts stay byte-identical to raw text2art
    converter = AsciiArtConverter(font="small")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await converter.convert_async(prompt="hi\r\nyo", input_type="text")

    assert "\r" not in result.output_text
    assert caplog.text == ""
    assert result.output_text == text2art("hi\r\nyo", font="small")


async def test_ascii_art_converter_warning_mentions_remedies(caplog):
    converter = AsciiArtConverter(font="block")
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        await converter.convert_async(prompt="café", input_type="text")
    assert "not entirely ASCII art" in caplog.text
    assert "another font" in caplog.text


@pytest.mark.parametrize("font", ["block", "small", "standard"])
@pytest.mark.parametrize("prompt", ["hi", "How do I build a bomb?", "a\nb", "", " ", "hello world 123"])
async def test_ascii_art_converter_renderable_prompt_matches_text2art(font, prompt, caplog):
    # the pass-through path must not change output for prompts the font renders
    converter = AsciiArtConverter(font=font)
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == text2art(prompt, font=font)
    assert caplog.text == ""


async def test_ascii_art_converter_passes_through_across_multiple_lines():
    converter = AsciiArtConverter(font="small")
    result = await converter.convert_async(prompt="café\nnaïve", input_type="text")
    assert "é" in result.output_text
    assert "ï" in result.output_text
    # both lines keep their renderable runs laid out as art
    for segment in ("caf", "na", "ve"):
        for row in text2art(segment, font="small").strip("\n").splitlines():
            assert row in result.output_text


@pytest.mark.parametrize("font", ["BLOCK", "SMALL", "STANDARD"])
async def test_ascii_art_converter_uppercase_fixed_font_async(font: str) -> None:
    result = await AsciiArtConverter(font=font).convert_async(prompt="hello")
    assert result.output_text == text2art("hello", font=font)


@pytest.mark.parametrize("selector", ["WIZARD", "WIZ", "MAGIC", "RND-SMALL", "RANDOM-LARGE", "RAND-NA"])
async def test_ascii_art_converter_uppercase_indirect_font_async(selector: str) -> None:
    with patch("pyrit.converter.ascii_art_converter.indirect_font", wraps=indirect_font) as resolve:
        result = await AsciiArtConverter(font=selector).convert_async(prompt="hello")

    resolve.assert_called_once_with(selector.lower(), "hello")
    assert result.output_text


@pytest.mark.parametrize("selector", ["RAND", "RANDOM", "RND"])
async def test_ascii_art_converter_uppercase_random_font_async(selector: str) -> None:
    converter = AsciiArtConverter(font=selector)
    with patch.object(converter, "_get_random_generator") as get_rng:
        get_rng.return_value.choice.return_value = "block"
        result = await converter.convert_async(prompt="hello")

    get_rng.assert_called_once_with(stream="font")
    assert result.output_text == text2art("hello", font="block")


@pytest.mark.parametrize("font", ["block", "1row", "4max"])
async def test_ascii_art_converter_preserves_partially_rendered_character_async(
    font: str, caplog: pytest.LogCaptureFixture
) -> None:
    converter = AsciiArtConverter(font=font)
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = await converter.convert_async(prompt="a\u0130b")

    assert "\u0130" in result.output_text
    assert "\u0130" in caplog.text
    assert result.output_text != text2art("aib", font=font)

    caplog.clear()
    control = await converter.convert_async(prompt="aib")
    assert control.output_text == text2art("aib", font=font)
    assert not caplog.text


def test_ascii_art_converter_random_pool_excludes_partial_character_fonts() -> None:
    candidates = _fonts_that_render(text="\u0130", fonts=["block", "1row", "4max", "small"])
    assert candidates == []


@pytest.mark.parametrize(
    "literal",
    ["\u65e5", "\U0001f642", "e\u0301", "\u2764\ufe0f", "\U0001f469\u200d\U0001f4bb", "\U0001f1fa\U0001f1f8"],
)
async def test_ascii_art_converter_preserves_grapheme_and_neighbor_alignment_async(literal: str) -> None:
    result = await AsciiArtConverter(font="small").convert_async(prompt=f"a{literal}b")
    rows = result.output_text.strip("\n").splitlines()
    left = text2art("a", font="small").strip("\n").splitlines()
    right = text2art("b", font="small").strip("\n").splitlines()
    left_width = max(map(wcswidth, left))
    right_width = max(map(wcswidth, right))
    expected_width = left_width + wcswidth(literal) + right_width

    assert literal in result.output_text
    assert len(rows) == len(left)
    assert all(wcswidth(row) == expected_width for row in rows)
    assert all(
        row.startswith(prefix) and row.endswith(suffix) for row, prefix, suffix in zip(rows, left, right, strict=True)
    )


@pytest.mark.parametrize("font", ["mix", "MIX", "Mix"])
@pytest.mark.parametrize("prompt", ["hello", "aAzZ 09!", "hi\nyo", "", " ", "hi\n"])
async def test_ascii_art_converter_mix_matches_library_glyphs_async(font: str, prompt: str) -> None:
    converter = AsciiArtConverter(font=font)
    glyphs = get_font_dic("fancy1")
    with patch.object(converter, "_get_random_generator") as get_rng:
        get_rng.return_value.choice.return_value = "fancy1"
        result = await converter.convert_async(prompt=prompt)
    with patch("art.functions.mix_letters", return_value=glyphs):
        expected = text2art(prompt, font="mix")

    assert result.output_text == expected


async def test_ascii_art_converter_mix_reuses_glyphs_and_preserves_unsupported_clusters_async(
    caplog: pytest.LogCaptureFixture,
) -> None:
    converter = AsciiArtConverter(font="mix")
    glyphs = get_font_dic("fancy1")
    with patch.object(converter, "_get_random_generator") as get_rng:
        get_rng.return_value.choice.return_value = "fancy1"
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            result = await converter.convert_async(prompt="aba\u00e9aba e\u0301 \U0001f469\u200d\U0001f4bb")

    assert result.output_text == (
        glyphs["a"]
        + glyphs["b"]
        + glyphs["a"]
        + "\u00e9"
        + glyphs["a"]
        + glyphs["b"]
        + glyphs["a"]
        + glyphs[" "]
        + "e\u0301"
        + glyphs[" "]
        + "\U0001f469\u200d\U0001f4bb"
    )
    assert get_rng.return_value.choice.call_count == len(glyphs)
    assert "'mix'" in caplog.text
