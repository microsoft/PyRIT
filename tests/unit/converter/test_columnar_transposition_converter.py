# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ColumnarTranspositionConverter, ConverterResult


async def test_columnar_transposition_known_vector():
    # Canonical Wikipedia example (incomplete-columns variant):
    # key "ZEBRAS", plaintext "WEAREDISCOVEREDFLEEATONCE" -> "EVLNACDTESEAROFODEECWIREE".
    converter = ColumnarTranspositionConverter(key="ZEBRAS")
    result = await converter.convert_async(prompt="WEAREDISCOVEREDFLEEATONCE", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "EVLNACDTESEAROFODEECWIREE"
    assert result.output_type == "text"


async def test_columnar_transposition_key_case_insensitive():
    lower = ColumnarTranspositionConverter(key="zebras")
    upper = ColumnarTranspositionConverter(key="ZEBRAS")
    result_lower = await lower.convert_async(prompt="WEAREDISCOVEREDFLEEATONCE", input_type="text")
    result_upper = await upper.convert_async(prompt="WEAREDISCOVEREDFLEEATONCE", input_type="text")
    assert result_lower.output_text == result_upper.output_text == "EVLNACDTESEAROFODEECWIREE"


async def test_columnar_transposition_repeated_key_letters_stable():
    # Repeated letters keep their original left-to-right order. Key "BAA" ranks as columns
    # [1, 2, 0]: the two 'A's (columns 1 and 2) come before 'B' (column 0), in that order.
    # "abcdef" grid (3 cols): col0="ad", col1="be", col2="cf" -> be + cf + ad = "becfad".
    converter = ColumnarTranspositionConverter(key="BAA")
    result = await converter.convert_async(prompt="abcdef", input_type="text")
    assert result.output_text == "becfad"


async def test_columnar_transposition_is_permutation():
    # A pure transposition only rearranges characters, so the multiset of characters is preserved.
    prompt = "Hello, World! 123"
    converter = ColumnarTranspositionConverter(key="SECRET")
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert sorted(result.output_text) == sorted(prompt)
    assert len(result.output_text) == len(prompt)


async def test_columnar_transposition_preserves_spaces_and_punctuation():
    # Every character (spaces, digits, punctuation) participates in the grid.
    prompt = "attack at dawn!"
    converter = ColumnarTranspositionConverter(key="KEY")
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text.count(" ") == prompt.count(" ")
    assert "!" in result.output_text


async def test_columnar_transposition_single_column_identity():
    # A one-letter key means a single column, so the text is returned unchanged.
    converter = ColumnarTranspositionConverter(key="A")
    result = await converter.convert_async(prompt="unchanged text", input_type="text")
    assert result.output_text == "unchanged text"


async def test_columnar_transposition_key_longer_than_text():
    # More columns than characters: trailing columns are simply empty, no padding is added.
    converter = ColumnarTranspositionConverter(key="ZEBRAS")
    result = await converter.convert_async(prompt="ab", input_type="text")
    # Columns: col0="a", col1="b", rest empty. Read order [4,2,1,3,5,0] -> "" "" "b" "" "" "a".
    assert result.output_text == "ba"


async def test_columnar_transposition_empty_prompt():
    converter = ColumnarTranspositionConverter(key="ZEBRAS")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_columnar_transposition_identifier_params():
    converter = ColumnarTranspositionConverter(key="ZEBRAS")
    identifier = converter.get_identifier()
    assert identifier.params["key"] == "ZEBRAS"


def test_columnar_transposition_invalid_empty_key():
    with pytest.raises(ValueError, match="columnar transposition key value invalid"):
        ColumnarTranspositionConverter(key="")


def test_columnar_transposition_invalid_non_alphabetic_key():
    with pytest.raises(ValueError, match="columnar transposition key value invalid"):
        ColumnarTranspositionConverter(key="key123")


def test_columnar_transposition_invalid_non_ascii_key():
    with pytest.raises(ValueError, match="columnar transposition key value invalid"):
        ColumnarTranspositionConverter(key="kéy")


async def test_columnar_transposition_input_not_supported():
    converter = ColumnarTranspositionConverter(key="ZEBRAS")
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
