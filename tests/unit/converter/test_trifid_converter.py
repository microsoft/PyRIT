# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, TrifidConverter


def _trifid_decode(converter: TrifidConverter, ciphertext: str) -> str:
    """
    Reference Trifid decryption used to verify round-trips in tests.

    It reuses the converter's own cube (``_symbol_to_coords`` / ``_coords_to_symbol``) and period,
    inverting the fractionation: for each group, concatenate every symbol's three coordinates, then
    split that digit stream into the layer / row / column rows and recombine per position.
    """
    cleaned = [char for char in ciphertext.upper() if char in converter._symbol_to_coords]
    period = converter._period

    plaintext: list[str] = []
    for start in range(0, len(cleaned), period):
        group = cleaned[start : start + period]
        digits: list[int] = []
        for symbol in group:
            digits.extend(converter._symbol_to_coords[symbol])

        n = len(group)
        layers = digits[0:n]
        rows = digits[n : 2 * n]
        columns = digits[2 * n : 3 * n]
        plaintext.extend(converter._coords_to_symbol[(layers[i], rows[i], columns[i])] for i in range(n))

    return "".join(plaintext)


async def test_trifid_converter_known_wikipedia_vector():
    # Canonical worked example from the Wikipedia Trifid cipher article.
    # Key phrase "FELIX MARIE DELASTELLE" builds the cube alphabet FELIXMARDSTBCGHJKNOPQUVWYZ+
    # and with period 5 the plaintext "aide toi le ciel t'aidera" encodes to FMJFVOISSUFTFPUFEQQC.
    converter = TrifidConverter(key="FELIX MARIE DELASTELLE", period=5)
    result = await converter.convert_async(prompt="aide toi le ciel t'aidera", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "FMJFVOISSUFTFPUFEQQC"
    assert result.output_type == "text"


async def test_trifid_converter_builds_wikipedia_alphabet():
    converter = TrifidConverter(key="FELIX MARIE DELASTELLE")
    assert converter._alphabet == "FELIXMARDSTBCGHJKNOPQUVWYZ+"


async def test_trifid_converter_default_alphabet_is_sequential():
    converter = TrifidConverter()
    assert converter._alphabet == "ABCDEFGHIJKLMNOPQRSTUVWXYZ+"


async def test_trifid_converter_round_trip_default():
    converter = TrifidConverter()
    plaintext = "THEQUICKBROWNFOXJUMPSOVERTHELAZYDOG"
    encoded = (await converter.convert_async(prompt=plaintext, input_type="text")).output_text
    assert _trifid_decode(converter, encoded) == plaintext


async def test_trifid_converter_round_trip_keyed_and_long_period():
    converter = TrifidConverter(key="SECRETKEY", period=7)
    plaintext = "ATTACKATDAWNWITHALLFORCES"
    encoded = (await converter.convert_async(prompt=plaintext, input_type="text")).output_text
    assert _trifid_decode(converter, encoded) == plaintext


async def test_trifid_converter_period_changes_output():
    plaintext = "DEFENDTHEEASTERNWALL"
    result_5 = (await TrifidConverter(period=5).convert_async(prompt=plaintext, input_type="text")).output_text
    result_3 = (await TrifidConverter(period=3).convert_async(prompt=plaintext, input_type="text")).output_text
    assert result_5 != result_3
    # Both remain decodable with their matching period.
    assert _trifid_decode(TrifidConverter(period=5), result_5) == plaintext
    assert _trifid_decode(TrifidConverter(period=3), result_3) == plaintext


async def test_trifid_converter_period_one_is_identity_substitution():
    # With period 1 each letter is fractionated and reassembled on its own, so the three coordinate
    # rows are read straight back in order -> Trifid degenerates to the identity on the cube alphabet.
    converter = TrifidConverter(period=1)
    result = await converter.convert_async(prompt="HELLO", input_type="text")
    assert result.output_text == "HELLO"


async def test_trifid_converter_drops_non_alphabet_characters():
    converter = TrifidConverter()
    # Spaces, digits and punctuation are removed; output equals encoding the stripped letters only.
    with_noise = (await converter.convert_async(prompt="He llo, 123!", input_type="text")).output_text
    without_noise = (await converter.convert_async(prompt="HELLO", input_type="text")).output_text
    assert with_noise == without_noise


async def test_trifid_converter_output_is_uppercase():
    converter = TrifidConverter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert result.output_text == result.output_text.upper()


async def test_trifid_converter_filler_symbol_is_encodable():
    # The filler is the 27th cube symbol, so it is a valid plaintext character (not dropped).
    converter = TrifidConverter(period=2)
    encoded = (await converter.convert_async(prompt="A+B", input_type="text")).output_text
    assert _trifid_decode(converter, encoded) == "A+B"


async def test_trifid_converter_empty_prompt():
    converter = TrifidConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_trifid_converter_identifier_includes_params():
    converter = TrifidConverter(key="SECRET", filler=".", period=4)
    params = converter.get_identifier().params
    assert params["key"] == "SECRET"
    assert params["filler"] == "."
    assert params["period"] == 4


def test_trifid_converter_invalid_multichar_filler():
    with pytest.raises(ValueError, match="filler"):
        TrifidConverter(filler="ab")


def test_trifid_converter_invalid_alphabetic_filler():
    with pytest.raises(ValueError, match="filler"):
        TrifidConverter(filler="a")


def test_trifid_converter_invalid_key_contains_filler():
    with pytest.raises(ValueError, match="filler"):
        TrifidConverter(key="SE+RET", filler="+")


def test_trifid_converter_invalid_period():
    with pytest.raises(ValueError, match="period"):
        TrifidConverter(period=0)


async def test_trifid_converter_input_not_supported():
    converter = TrifidConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
