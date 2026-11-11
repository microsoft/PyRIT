# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import unicodedata

import pytest

from pyrit.converter import ConverterResult, FullwidthConverter, WordIndexSelectionStrategy


async def test_fullwidth_converts_letters_digits_and_punctuation_async() -> None:
    result = await FullwidthConverter().convert_async(prompt="Hello, World 123!", input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == "Ｈｅｌｌｏ， Ｗｏｒｌｄ １２３！"


async def test_fullwidth_round_trips_through_nfkc_for_all_printable_ascii_async() -> None:
    printable_ascii = "".join(chr(code) for code in range(0x21, 0x7F))

    result = await FullwidthConverter().convert_async(prompt=printable_ascii, input_type="text")

    assert result.output_text != printable_ascii
    assert all(0xFF01 <= ord(char) <= 0xFF5E for char in result.output_text)
    assert unicodedata.normalize("NFKC", result.output_text) == printable_ascii


async def test_fullwidth_leaves_non_ascii_and_separators_unchanged_async() -> None:
    result = await FullwidthConverter().convert_async(prompt="café 日本語", input_type="text")

    assert result.output_text == "ｃａｆé 日本語"


async def test_fullwidth_respects_word_selection_strategy_async() -> None:
    converter = FullwidthConverter(word_selection_strategy=WordIndexSelectionStrategy(indices=[1]))

    result = await converter.convert_async(prompt="keep change keep", input_type="text")

    assert result.output_text == "keep ｃｈａｎｇｅ keep"


async def test_fullwidth_empty_prompt_returns_empty_text_async() -> None:
    result = await FullwidthConverter().convert_async(prompt="", input_type="text")

    assert result.output_text == ""


async def test_fullwidth_rejects_unsupported_input_type_async() -> None:
    with pytest.raises(ValueError):
        await FullwidthConverter().convert_async(prompt="hello", input_type="image_path")


def test_fullwidth_converter_identifier_is_stable() -> None:
    first = FullwidthConverter().get_identifier()
    second = FullwidthConverter().get_identifier()

    assert first.class_name == "FullwidthConverter"
    assert first.hash == second.hash
