# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, TapCodeConverter


async def test_tap_code_converter_single_letter():
    converter = TapCodeConverter()
    result = await converter.convert_async(prompt="A", input_type="text")
    assert isinstance(result, ConverterResult)
    # A is at (row 1, column 1).
    assert result.output_text == ". ."
    assert result.output_type == "text"


async def test_tap_code_converter_known_example():
    # Standard tap-code worked example: WATER.
    # W=(5,2) A=(1,1) T=(4,4) E=(1,5) R=(4,2)
    converter = TapCodeConverter()
    result = await converter.convert_async(prompt="WATER", input_type="text")
    assert result.output_text == "..... ..  . .  .... ....  . .....  .... .."
    assert result.output_type == "text"


async def test_tap_code_converter_corner_letters():
    converter = TapCodeConverter()
    # A=(1,1), E=(1,5), V=(5,1), Z=(5,5)
    result = await converter.convert_async(prompt="AEVZ", input_type="text")
    assert result.output_text == ". .  . .....  ..... .  ..... ....."


async def test_tap_code_converter_c_and_k_share_cell():
    converter = TapCodeConverter()
    c_result = await converter.convert_async(prompt="C", input_type="text")
    k_result = await converter.convert_async(prompt="K", input_type="text")
    # Both C and K map to (row 1, column 3).
    assert c_result.output_text == ". ..."
    assert k_result.output_text == ". ..."
    assert c_result.output_text == k_result.output_text


async def test_tap_code_converter_case_insensitive():
    converter = TapCodeConverter()
    lower = await converter.convert_async(prompt="water", input_type="text")
    upper = await converter.convert_async(prompt="WATER", input_type="text")
    assert lower.output_text == upper.output_text


async def test_tap_code_converter_preserves_non_letters():
    converter = TapCodeConverter()
    result = await converter.convert_async(prompt="A1!", input_type="text")
    # The digit and punctuation pass through verbatim, joined by the letter separator.
    assert result.output_text == ". .  1  !"


async def test_tap_code_converter_custom_symbols():
    converter = TapCodeConverter(tap_symbol="*", group_separator="-", letter_separator="/")
    result = await converter.convert_async(prompt="AB", input_type="text")
    # A=(1,1)->"*-*", B=(1,2)->"*-**"
    assert result.output_text == "*-*/*-**"


async def test_tap_code_converter_empty():
    converter = TapCodeConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_tap_code_converter_identifier_includes_params():
    converter = TapCodeConverter(tap_symbol="*", group_separator="-", letter_separator="/")
    identifier = converter.get_identifier()
    params = identifier.params
    assert params["tap_symbol"] == "*"
    assert params["group_separator"] == "-"
    assert params["letter_separator"] == "/"


async def test_tap_code_converter_input_not_supported():
    converter = TapCodeConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
