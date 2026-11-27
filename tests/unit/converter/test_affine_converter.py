# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import AffineConverter, ConverterResult


async def test_affine_converter_wikipedia_vector():
    # Published Wikipedia test vector: a=5, b=8 maps "AFFINECIPHER" -> "IHHWVCSWFRCP".
    converter = AffineConverter(a=5, b=8)
    result = await converter.convert_async(prompt="AFFINECIPHER", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "IHHWVCSWFRCP"
    assert result.output_type == "text"


async def test_affine_converter_defaults_match_wikipedia_vector():
    # Defaults are a=5, b=8.
    converter = AffineConverter()
    result = await converter.convert_async(prompt="AFFINECIPHER", input_type="text")
    assert result.output_text == "IHHWVCSWFRCP"


async def test_affine_converter_caesar_special_case():
    # a=1 reduces the Affine cipher to a Caesar shift of b.
    converter = AffineConverter(a=1, b=1)
    result = await converter.convert_async(prompt="abc", input_type="text")
    assert result.output_text == "bcd"


async def test_affine_converter_atbash_special_case():
    # a=25, b=25 reduces the Affine cipher to Atbash.
    converter = AffineConverter(a=25, b=25)
    result = await converter.convert_async(prompt="abcz", input_type="text")
    assert result.output_text == "zyxa"


async def test_affine_converter_preserves_case():
    converter = AffineConverter(a=5, b=8)
    result = await converter.convert_async(prompt="AbC", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "InS"
    assert result.output_type == "text"


async def test_affine_converter_preserves_non_alpha():
    converter = AffineConverter(a=5, b=8)
    result = await converter.convert_async(prompt="Hello, 123!", input_type="text")
    # Digits, punctuation, and whitespace pass through unchanged.
    assert result.output_text == "Rclla, 123!"


async def test_affine_converter_roundtrip_decrypt():
    # Enciphering with the modular inverse of a recovers the plaintext.
    import string

    plaintext = string.ascii_lowercase
    enc = await AffineConverter(a=7, b=3).convert_async(prompt=plaintext, input_type="text")
    # a=15 is the modular inverse of 7 mod 26; combined with the matching shift it inverts the map.
    dec = await AffineConverter(a=15, b=-45).convert_async(prompt=enc.output_text, input_type="text")
    assert dec.output_text == plaintext


async def test_affine_converter_b_normalized_mod_26():
    # b is taken modulo 26, so b and b+26 are equivalent.
    a = await AffineConverter(a=5, b=8).convert_async(prompt="hello", input_type="text")
    b = await AffineConverter(a=5, b=34).convert_async(prompt="hello", input_type="text")
    assert a.output_text == b.output_text


async def test_affine_converter_with_description():
    converter = AffineConverter(a=5, b=8, append_description=True)
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # The encoded prompt ("hello" -> "rclla") should be present in the output.
    assert "rclla" in result.output_text


def test_affine_converter_invalid_multiplier_even():
    with pytest.raises(ValueError, match="coprime to 26"):
        AffineConverter(a=2, b=3)


def test_affine_converter_invalid_multiplier_thirteen():
    with pytest.raises(ValueError, match="coprime to 26"):
        AffineConverter(a=13, b=3)


async def test_affine_converter_empty():
    converter = AffineConverter(a=5, b=8)
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_affine_converter_input_not_supported():
    converter = AffineConverter(a=5, b=8)
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
