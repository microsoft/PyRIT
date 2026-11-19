# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, PlayfairConverter


async def test_playfair_converter_known_vector():
    # Canonical Wikipedia example: key "playfair example", I/J merged, 'x' filler.
    # "Hide the gold in the tree stump" -> BMODZBXDNABEKUDMUIXMMOUVIF
    converter = PlayfairConverter(key="playfair example")
    result = await converter.convert_async(prompt="Hide the gold in the tree stump", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "BMODZBXDNABEKUDMUIXMMOUVIF"
    assert result.output_type == "text"


async def test_playfair_converter_output_is_uppercase_letters_only():
    converter = PlayfairConverter(key="keyword")
    result = await converter.convert_async(prompt="Hello, World! 123", input_type="text")
    assert result.output_text.isalpha()
    assert result.output_text.isupper()


async def test_playfair_converter_drops_non_alphabetic():
    converter = PlayfairConverter(key="keyword")
    with_punct = await converter.convert_async(prompt="at tack!", input_type="text")
    without_punct = await converter.convert_async(prompt="attack", input_type="text")
    # Spaces and punctuation are removed before encoding, so both reduce to the same letters.
    assert with_punct.output_text == without_punct.output_text


async def test_playfair_converter_doubled_letters_get_filler():
    # "tree" -> t r | e e: the doubled 'ee' is split by a filler and the now-odd tail is padded,
    # so 4 input letters become 3 digraphs (6 ciphertext letters) rather than 4.
    converter = PlayfairConverter(key="keyword")
    result = await converter.convert_async(prompt="tree", input_type="text")
    # t r | e (x) | e (x) -> 3 digraphs -> 6 letters.
    assert len(result.output_text) == 6


async def test_playfair_converter_odd_length_is_padded():
    converter = PlayfairConverter(key="keyword")
    result = await converter.convert_async(prompt="abc", input_type="text")
    # 3 letters -> padded to 4 -> 2 digraphs -> 4 ciphertext letters.
    assert len(result.output_text) == 4


async def test_playfair_converter_merge_letter_folds_in():
    converter = PlayfairConverter(key="keyword")
    # 'j' is merged into 'i' by default, so "jab" and "iab" encode identically.
    j_result = await converter.convert_async(prompt="jab", input_type="text")
    i_result = await converter.convert_async(prompt="iab", input_type="text")
    assert j_result.output_text == i_result.output_text


async def test_playfair_converter_same_row_rule():
    # With the plain alphabet key, row 0 is A B C D E. "ab" share a row -> each shifts right:
    # A->B, B->C, giving "BC".
    converter = PlayfairConverter(key="abcdefghiklmnopqrstuvwxyz")
    result = await converter.convert_async(prompt="ab", input_type="text")
    assert result.output_text == "BC"


async def test_playfair_converter_same_row_wraps():
    # Row 0 is A B C D E. "ae" share a row; E is last so wraps to A: A->B, E->A -> "BA".
    converter = PlayfairConverter(key="abcdefghiklmnopqrstuvwxyz")
    result = await converter.convert_async(prompt="ae", input_type="text")
    assert result.output_text == "BA"


async def test_playfair_converter_same_column_rule():
    # Column 0 of the plain-alphabet square is A F L Q V. "af" share a column -> each shifts down:
    # A->F, F->L, giving "FL".
    converter = PlayfairConverter(key="abcdefghiklmnopqrstuvwxyz")
    result = await converter.convert_async(prompt="af", input_type="text")
    assert result.output_text == "FL"


async def test_playfair_converter_key_changes_output():
    first = PlayfairConverter(key="alpha")
    second = PlayfairConverter(key="omega")
    first_result = await first.convert_async(prompt="attackatdawn", input_type="text")
    second_result = await second.convert_async(prompt="attackatdawn", input_type="text")
    assert first_result.output_text != second_result.output_text


async def test_playfair_converter_custom_padding_char():
    converter = PlayfairConverter(key="keyword", padding_char="z")
    # The doubled 'l' in "ball" is split by the custom 'z' filler, not 'x'. We just confirm it
    # differs from the default-filler result.
    default_converter = PlayfairConverter(key="keyword")
    custom = await converter.convert_async(prompt="ball", input_type="text")
    default = await default_converter.convert_async(prompt="ball", input_type="text")
    assert custom.output_text != default.output_text


async def test_playfair_converter_with_description():
    converter = PlayfairConverter(key="playfair example", append_description=True)
    result = await converter.convert_async(prompt="Hide the gold in the tree stump", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert "BMODZBXDNABEKUDMUIXMMOUVIF" in result.output_text
    assert "Playfair" in result.output_text


async def test_playfair_converter_empty_prompt():
    converter = PlayfairConverter(key="keyword")
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_playfair_converter_non_ascii_passthrough_dropped():
    # Non-ASCII letters are not part of the square and are dropped like other non-cipher chars.
    converter = PlayfairConverter(key="keyword")
    accented = await converter.convert_async(prompt="café", input_type="text")
    plain = await converter.convert_async(prompt="caf", input_type="text")
    assert accented.output_text == plain.output_text


def test_playfair_converter_invalid_empty_key():
    with pytest.raises(ValueError, match="playfair key value invalid"):
        PlayfairConverter(key="")


def test_playfair_converter_invalid_letterless_key():
    with pytest.raises(ValueError, match="playfair key value invalid"):
        PlayfairConverter(key="123 !!")


async def test_playfair_converter_key_ignores_non_letters():
    # A phrase key and its letters-only equivalent build the same square, so they encode alike.
    phrase = PlayfairConverter(key="playfair example")
    letters_only = PlayfairConverter(key="playfairexample")
    phrase_result = await phrase.convert_async(prompt="attackatdawn", input_type="text")
    letters_result = await letters_only.convert_async(prompt="attackatdawn", input_type="text")
    assert phrase_result.output_text == letters_result.output_text


def test_playfair_converter_invalid_merge_letter_length():
    with pytest.raises(ValueError, match="playfair merge_letter"):
        PlayfairConverter(key="keyword", merge_letter="jk")


def test_playfair_converter_merge_letter_equals_target():
    with pytest.raises(ValueError, match="must be different"):
        PlayfairConverter(key="keyword", merge_letter="i", merge_target="i")


def test_playfair_converter_padding_cannot_be_merged_letter():
    with pytest.raises(ValueError, match="padding_char cannot be the merged-away letter"):
        PlayfairConverter(key="keyword", merge_letter="j", padding_char="j")


async def test_playfair_converter_input_not_supported():
    converter = PlayfairConverter(key="keyword")
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
