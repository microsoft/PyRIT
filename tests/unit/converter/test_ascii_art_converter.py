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
