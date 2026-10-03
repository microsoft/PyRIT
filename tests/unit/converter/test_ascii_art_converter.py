# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

pytest.importorskip("art")

from art import FONT_NAMES
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


async def test_ascii_art_converter_random_font_preserves_art_candidate_pool():
    converter = AsciiArtConverter()

    with patch.object(converter, "_get_random_generator") as mock_get_rng:
        mock_get_rng.return_value.choice.return_value = "block"
        await converter.convert_async(prompt="test", input_type="text")

    candidates = mock_get_rng.return_value.choice.call_args.args[0]
    assert set(candidates) == set(FONT_NAMES) - set(RANDOM_FILTERED_FONTS)


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


async def test_ascii_art_converter_random_font_rejects_all_fonts_dropped_character():
    converter = AsciiArtConverter()

    with patch.object(converter, "_get_random_generator") as mock_get_rng:
        mock_get_rng.return_value.choice.return_value = "block"
        with pytest.raises(ValueError, match="font 'block'.*'é'"):
            await converter.convert_async(prompt="café", input_type="text")


async def test_ascii_art_converter_error_mentions_remedies():
    converter = AsciiArtConverter(font="block")
    with pytest.raises(ValueError) as exc_info:
        await converter.convert_async(prompt="café", input_type="text")
    message = str(exc_info.value)
    assert "silently" in message
    assert "another font" in message
