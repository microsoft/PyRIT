# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.converter.colloquial_wordswap_converter import (
    ColloquialWordswapConverter,
)
from pyrit.registry import ConverterRegistry


# Test for deterministic mode
@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        ("grandfather", "ah gong"),  # Single wordswap
        ("mother and brother", "mama and bro"),  # Default substitution for mother and brother
        ("Hello, my Father!", "Hello, my papa!"),  # Combined substitutions with punctuation
    ],
)
async def test_colloquial_deterministic(input_text, expected_output):
    converter = ColloquialWordswapConverter(deterministic=True)
    result = await converter.convert_async(prompt=input_text)
    assert result.output_text == expected_output


# Test for non-deterministic mode
@pytest.mark.parametrize(
    "input_text",
    [
        "code",  # A different input set to reduce redundancy
        "mother",
        "uncle and brother",
    ],
)
async def test_colloquial_non_deterministic(input_text):
    converter = ColloquialWordswapConverter(deterministic=False)
    result = await converter.convert_async(prompt=input_text)

    # Valid substitution mappings in the input texts
    valid_substitutions = {
        "mother": ["mama", "amma", "ibu"],
        "uncle": ["encik", "unker"],
        "brother": ["bro", "boiboi", "di di", "xdd", "anneh", "thambi"],
    }

    # Split input and output into words, preserving multi-word substitutions as single tokens
    input_words = re.findall(r"\w+|\S+", input_text)
    output_words = re.findall(r"\w+|\S+", result.output_text)

    # Check that each wordswap is a valid substitution
    for input_word, output_word in zip(input_words, output_words, strict=False):
        lower_input_word = input_word.lower()

        if lower_input_word in valid_substitutions:
            assert any(sub in output_word or output_word in sub for sub in valid_substitutions[lower_input_word])
        else:
            assert output_word == input_word


# Test for custom substitutions dict
@pytest.mark.parametrize(
    "input_text,custom_substitutions,expected_output",
    [
        ("father", {"father": ["appa", "darth vader"]}, "appa"),
        ("Hello world", {"hello": ["hi", "hey"], "world": ["earth"]}, "hi earth"),
    ],
)
async def test_colloquial_custom_substitutions(input_text, custom_substitutions, expected_output):
    converter = ColloquialWordswapConverter(deterministic=True, custom_substitutions=custom_substitutions)
    result = await converter.convert_async(prompt=input_text)
    assert result.output_text == expected_output


# Test for non-default wordswap YAML paths
@pytest.mark.parametrize(
    "input_text,wordswap_path,expected_output",
    [
        ("father", "filipino.yaml", "papa"),
        ("woman", "indian.yaml", "behenji"),
        ("son", "southern_american.yaml", "junior"),
        ("man", "multicultural_london.yaml", "mandem"),
    ],
)
async def test_colloquial_wordswap_path(input_text, wordswap_path, expected_output):
    converter = ColloquialWordswapConverter(deterministic=True, wordswap_path=wordswap_path)
    result = await converter.convert_async(prompt=input_text)
    assert result.output_text == expected_output


# Test multiple word prompts for non-default wordswap paths
@pytest.mark.parametrize(
    "input_text,wordswap_path,expected_output",
    [
        ("father and mother", "indian.yaml", "papa and mummy"),
        ("brother and sister", "southern_american.yaml", "bubba and sissy"),
        ("aunt and uncle", "multicultural_london.yaml", "aunty and unc"),
    ],
)
async def test_multiple_words_custom_colloquialisms(input_text, wordswap_path, expected_output):
    converter = ColloquialWordswapConverter(deterministic=True, wordswap_path=wordswap_path)
    result = await converter.convert_async(prompt=input_text)
    assert result.output_text == expected_output


# Test default behavior when no wordswap_path or custom_substitutions given
async def test_colloquial_default_singaporean():
    converter = ColloquialWordswapConverter(deterministic=True)
    result = await converter.convert_async(prompt="mother and father")
    assert result.output_text == "mama and papa"


# Test that empty custom_substitutions falls through to defaults
async def test_colloquial_empty_custom_substitutions():
    converter = ColloquialWordswapConverter(deterministic=True, custom_substitutions={})
    result = await converter.convert_async(prompt="mother and father")
    assert result.output_text == "mama and papa"


# Test multiple word prompts
@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        ("father and mother", "papa and mama"),
        ("brother and sister", "bro and xjj"),
        ("aunt and uncle", "makcik and encik"),
    ],
)
async def test_multiple_words(input_text, expected_output):
    converter = ColloquialWordswapConverter(deterministic=True)
    result = await converter.convert_async(prompt=input_text)
    assert result.output_text == expected_output


# Test for awkward spacing
@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        ("  father  and    mother ", "papa and mama"),
        ("sister   and   brother", "xjj and bro"),
    ],
)
async def test_awkward_spacing(input_text, expected_output):
    converter = ColloquialWordswapConverter(deterministic=True)
    result = await converter.convert_async(prompt=input_text)
    assert result.output_text == expected_output


# Test for punctuation handling
@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        ("father, mother!", "papa, mama!"),
        ("aunt? uncle!", "makcik? encik!"),
    ],
)
async def test_punctuation_handling(input_text, expected_output):
    converter = ColloquialWordswapConverter(deterministic=True)
    result = await converter.convert_async(prompt=input_text)
    assert result.output_text == expected_output


@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        ("Ask (father) first.", "Ask (papa) first."),
        ('"mother" said hi', '"mama" said hi'),
        ("My father-in-law called.", "My papa-in-law called."),
        ("e-mail your brother", "e-mail your bro"),
        ("a 'quoted' word", "a 'quoted' word"),
    ],
)
async def test_punctuation_attached_to_words(input_text, expected_output):
    converter = ColloquialWordswapConverter(deterministic=True)
    result = await converter.convert_async(prompt=input_text)
    assert result.output_text == expected_output


def test_colloquial_converter_input_supported() -> None:
    converter = ColloquialWordswapConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_init_conflict_custom_substitutions_and_path():
    with pytest.raises(ValueError, match="Provide either custom_substitutions or wordswap_path"):
        ColloquialWordswapConverter(custom_substitutions={"foo": ["bar"]}, wordswap_path="some_file.yaml")


def test_init_missing_yaml_file():
    with pytest.raises(FileNotFoundError, match="Colloquial wordswap file not found"):
        ColloquialWordswapConverter(wordswap_path="nonexistent.yaml")


def test_init_invalid_yaml_format(tmp_path):
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("[not, a, dict]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a dict"):
        ColloquialWordswapConverter(wordswap_path=str(bad_yaml))


def test_init_invalid_yaml_values(tmp_path):
    bad_values = tmp_path / "bad_values.yaml"
    bad_values.write_text('father: "not a list"', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid entry in wordswap YAML"):
        ColloquialWordswapConverter(wordswap_path=str(bad_values))


def test_init_invalid_yaml_empty_list(tmp_path):
    empty_list = tmp_path / "empty_list.yaml"
    empty_list.write_text("father: []", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid entry in wordswap YAML"):
        ColloquialWordswapConverter(wordswap_path=str(empty_list))


def test_init_malformed_yaml(tmp_path):
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("{{invalid yaml::", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid YAML format"):
        ColloquialWordswapConverter(wordswap_path=str(malformed))


def test_identifier_distinguishes_custom_substitution_values() -> None:
    # Same keys, different substitutions: the converters produce different output.
    papa = ColloquialWordswapConverter(deterministic=True, custom_substitutions={"father": ["papa"]})
    bapa = ColloquialWordswapConverter(deterministic=True, custom_substitutions={"father": ["bapa"]})
    assert papa.get_identifier().hash != bapa.get_identifier().hash

    # Both configurations get their own default registry name instead of colliding.
    instances = ConverterRegistry().instances
    instances.register(papa)
    instances.register(bapa)
    assert instances.get(papa.get_identifier().unique_name) is papa
    assert instances.get(bapa.get_identifier().unique_name) is bapa


def test_identifier_is_stable_for_equal_custom_substitutions() -> None:
    first = ColloquialWordswapConverter(custom_substitutions={"father": ["papa", "pa"], "mother": ["mama"]})
    second = ColloquialWordswapConverter(custom_substitutions={"mother": ["mama"], "father": ["papa", "pa"]})
    assert first.get_identifier().hash == second.get_identifier().hash
    assert len(first.get_identifier().params["custom_substitutions_hash"]) == 16


def test_identifier_identifies_wordswap_files_by_path() -> None:
    # Built-in and file-based substitutions keep their path-based identity.
    assert "custom_substitutions_hash" not in ColloquialWordswapConverter().get_identifier().params
    assert (
        "custom_substitutions_hash" not in ColloquialWordswapConverter(custom_substitutions={}).get_identifier().params
    )
    assert (
        "custom_substitutions_hash"
        not in ColloquialWordswapConverter(wordswap_path="filipino.yaml").get_identifier().params
    )
