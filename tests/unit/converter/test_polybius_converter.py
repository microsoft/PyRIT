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
    # Letters encode; digits, spaces, punctuation pass through unchanged.
    assert result.output_text == "111 12!"


async def test_polybius_converter_word_boundary_grouping():
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="AB CD", input_type="text")
    # Space ends a run of letters and passes through verbatim.
    assert result.output_text == "11 12 13 14"


async def test_polybius_converter_custom_coordinate_separator():
    converter = PolybiusConverter(coordinate_separator="-")
    result = await converter.convert_async(prompt="AB", input_type="text")
    assert result.output_text == "1-1 1-2"


async def test_polybius_converter_custom_letter_separator():
    converter = PolybiusConverter(letter_separator=".")
    result = await converter.convert_async(prompt="AB", input_type="text")
    assert result.output_text == "11.12"


async def test_polybius_converter_empty_letter_separator():
    converter = PolybiusConverter(letter_separator="")
    result = await converter.convert_async(prompt="AB", input_type="text")
    assert result.output_text == "1112"


async def test_polybius_converter_custom_merge_letters():
    # Merge C and K instead of I and J: the grid omits K, so J keeps its own cell.
    converter = PolybiusConverter(merge_letters=("C", "K"))
    result = await converter.convert_async(prompt="CK", input_type="text")
    pair = result.output_text.split(" ")
    assert pair[0] == pair[1]


async def test_polybius_converter_empty():
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_polybius_converter_only_non_letters():
    converter = PolybiusConverter()
    result = await converter.convert_async(prompt="123 !?", input_type="text")
    assert result.output_text == "123 !?"


async def test_polybius_converter_invalid_merge_letters_non_alpha():
    with pytest.raises(ValueError):
        PolybiusConverter(merge_letters=("I", "5"))


async def test_polybius_converter_invalid_merge_letters_identical():
    with pytest.raises(ValueError):
        PolybiusConverter(merge_letters=("I", "I"))


async def test_polybius_converter_identifier_includes_params():
    converter = PolybiusConverter(coordinate_separator="-", letter_separator="/")
    params = converter.get_identifier().params
    assert params["coordinate_separator"] == "-"
    assert params["letter_separator"] == "/"
    assert list(params["merge_letters"]) == ["I", "J"]


async def test_polybius_converter_input_not_supported():
    converter = PolybiusConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
