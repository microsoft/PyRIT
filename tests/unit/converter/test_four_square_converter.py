# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, FourSquareConverter


async def test_four_square_wikipedia_known_answer():
    # Canonical worked example from the Wikipedia "Four-square cipher" article.
    # Keywords "example" / "keyword", the letter Q omitted (I and J kept separate),
    # plaintext "help me obiwan kenobi" -> "FYGMKYHOBXMFKKKIMD".
    converter = FourSquareConverter(
        top_right_key="example", bottom_left_key="keyword", merge_letter="Q", merge_target="O"
    )
    result = await converter.convert_async(prompt="help me obiwan kenobi", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "FYGMKYHOBXMFKKKIMD"
    assert result.output_type == "text"


async def test_four_square_output_is_uppercase_and_letters_only():
    converter = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    result = await converter.convert_async(prompt="Hello, World! 123", input_type="text")
    # Non-letters (comma, space, digits) are removed; output is uppercase ciphertext.
    assert result.output_text.isupper()
    assert result.output_text.isalpha()


async def test_four_square_non_letters_removed_match():
    # Punctuation/spaces/digits are stripped, so these two inputs encipher identically.
    converter = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    with_noise = await converter.convert_async(prompt="he, llo! world 42", input_type="text")
    clean = await converter.convert_async(prompt="helloworld", input_type="text")
    assert with_noise.output_text == clean.output_text


async def test_four_square_case_insensitive_input():
    converter = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    lower = await converter.convert_async(prompt="hello world", input_type="text")
    upper = await converter.convert_async(prompt="HELLO WORLD", input_type="text")
    assert lower.output_text == upper.output_text == "FYGFHXHQKK"


async def test_four_square_odd_length_is_padded():
    # A single letter is padded to one digraph, producing two ciphertext letters.
    converter = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    result = await converter.convert_async(prompt="a", input_type="text")
    assert len(result.output_text) == 2
    assert result.output_text == "AT"


async def test_four_square_default_merges_j_into_i():
    # With the default J->I merge, "jit" and "iit" encipher to the same ciphertext.
    converter = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    merged = await converter.convert_async(prompt="jit", input_type="text")
    target = await converter.convert_async(prompt="iit", input_type="text")
    assert merged.output_text == target.output_text


async def test_four_square_different_keys_differ():
    a = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    b = FourSquareConverter(top_right_key="keyword", bottom_left_key="example")
    ra = await a.convert_async(prompt="attack at dawn", input_type="text")
    rb = await b.convert_async(prompt="attack at dawn", input_type="text")
    assert ra.output_text != rb.output_text


async def test_four_square_empty_prompt():
    converter = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_four_square_non_ascii_letters_removed():
    # Non-ASCII letters are not part of the 5x5 alphabet and are removed, not raised on.
    converter = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    result = await converter.convert_async(prompt="café résumé", input_type="text")
    assert result.output_text.isalpha() or result.output_text == ""
    # "caférésumé" -> ASCII letters "cafrsum" (7) -> padded to "cafrsumx" (8 -> 4 digraphs)
    assert len(result.output_text) == 8


def test_four_square_invalid_empty_key():
    with pytest.raises(ValueError, match="top_right_key invalid"):
        FourSquareConverter(top_right_key="", bottom_left_key="keyword")
    with pytest.raises(ValueError, match="bottom_left_key invalid"):
        FourSquareConverter(top_right_key="example", bottom_left_key="")


def test_four_square_invalid_non_alphabetic_key():
    with pytest.raises(ValueError, match="top_right_key invalid"):
        FourSquareConverter(top_right_key="key123", bottom_left_key="keyword")


def test_four_square_invalid_merge_letter_not_single():
    with pytest.raises(ValueError, match="merge_letter must be a single ASCII letter"):
        FourSquareConverter(top_right_key="example", bottom_left_key="keyword", merge_letter="JK")


def test_four_square_invalid_merge_letter_equals_target():
    with pytest.raises(ValueError, match="merge_letter and merge_target must differ"):
        FourSquareConverter(top_right_key="example", bottom_left_key="keyword", merge_letter="J", merge_target="J")


def test_four_square_invalid_padding_is_merged_letter():
    # Padding char defaults to X; if the merged-away letter is X, padding X is invalid.
    with pytest.raises(ValueError, match="padding_char cannot be the merged-away letter"):
        FourSquareConverter(top_right_key="example", bottom_left_key="keyword", merge_letter="X", merge_target="W")


async def test_four_square_input_not_supported():
    converter = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")


def test_four_square_identifier_includes_keys():
    converter = FourSquareConverter(top_right_key="example", bottom_left_key="keyword")
    params = converter._build_identifier().params
    assert params["top_right_key"] == "example"
    assert params["bottom_left_key"] == "keyword"
    assert params["merge_letter"] == "J"
    assert params["merge_target"] == "I"
    assert params["padding_char"] == "X"
