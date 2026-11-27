# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, PolybiusConverter


async def test_polybius_converter_basic():
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="AB", input_type="text")
    assert isinstance(result, ConverterResult)
    # A -> row 1 col 1 -> "11"; B -> row 1 col 2 -> "12"
    assert result.output_text == "11 12"
    assert result.output_type == "text"


async def test_polybius_converter_known_vector():
    # Standard 5x5 square with I/J merged:
    #    1 2 3 4 5
    # 1  A B C D E
    # 2  F G H I K
    # 3  L M N O P
    # 4  Q R S T U
    # 5  V W X Y Z
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="POLYBIUS", input_type="text")
    # P=35 O=34 L=31 Y=54 B=12 I=24 U=45 S=43
    assert result.output_text == "35 34 31 54 12 24 45 43"


async def test_polybius_converter_corner_letters():
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="AEVZ", input_type="text")
    # A=11 (top-left), E=15 (top-right), V=51 (bottom-left), Z=55 (bottom-right)
    assert result.output_text == "11 15 51 55"


async def test_polybius_converter_ij_merged():
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="IJ", input_type="text")
    # Both I and J map to the same cell (24)
    assert result.output_text == "24 24"


async def test_polybius_converter_case_insensitive():
    converter = PolybiusConverter()
    lower = await converter.convert_async(prompt="hello", input_type="text")
    upper = await converter.convert_async(prompt="HELLO", input_type="text")
    assert lower.output_text == upper.output_text
    # H=23 E=15 L=31 L=31 O=34
    assert lower.output_text == "23 15 31 31 34"


async def test_polybius_converter_preserves_non_letters():
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="A1 B!", input_type="text")
    # Each character is its own space-separated token, so a literal digit ("1") stays distinct
    # from a coordinate pair ("11"), and the "/" marks the word boundary.
    assert result.output_text == "11 1 / 12 !"


async def test_polybius_converter_word_boundary_grouping():
    converter = PolybiusConverter()
    spaced = await converter.convert_async(prompt="AB CD", input_type="text")
    joined = await converter.convert_async(prompt="ABCD", input_type="text")
    # A space becomes an explicit "/" word token, so the two inputs no longer collide.
    assert spaced.output_text == "11 12 / 13 14"
    assert joined.output_text == "11 12 13 14"
    assert spaced.output_text != joined.output_text


async def test_polybius_converter_digit_letter_order_distinct():
    converter = PolybiusConverter()
    # "A1" and "1A" encoded different coordinate/digit orders rather than colliding on "111".
    a_then_1 = await converter.convert_async(prompt="A1", input_type="text")
    one_then_a = await converter.convert_async(prompt="1A", input_type="text")
    assert a_then_1.output_text == "11 1"
    assert one_then_a.output_text == "1 11"
    assert a_then_1.output_text != one_then_a.output_text


async def test_polybius_converter_empty():
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_polybius_converter_only_non_letters():
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="123 !?", input_type="text")
    # Every character is its own token; the space becomes the "/" word token.
    assert result.output_text == "1 2 3 / ! ?"


async def test_polybius_converter_to_ascii_equivalent_folds_j_to_i():
    # A correctly decoded prompt answers with I for each J (J shares I's cell).
    assert PolybiusConverter.to_ascii_equivalent(text="Jack jumped") == "Iack iumped"


async def test_polybius_converter_to_ascii_equivalent_none_without_j():
    # No J means nothing changes, so the hook returns None and the scenario keeps the exact seed.
    assert PolybiusConverter.to_ascii_equivalent(text="hello world") is None


async def test_polybius_converter_identifier_records_word_split_separator():
    converter = PolybiusConverter()
    params = converter.get_identifier().params
    assert params["word_split_separator"] == " "


async def test_polybius_converter_input_not_supported():
    converter = PolybiusConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
