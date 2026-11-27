# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio

import pytest

from pyrit.converter import PigLatinConverter


@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        # Simple consonant-initial words: move the single leading consonant and add "ay".
        ("hello", "ellohay"),
        ("pig", "igpay"),
        # Leading consonant *cluster* moves together.
        ("string", "ingstray"),
        ("glove", "oveglay"),
        ("three", "eethray"),
    ],
)
def test_consonant_cluster(input_text, expected_output):
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        ("apple", "appleway"),
        ("egg", "eggway"),
        ("industry", "industryway"),
        ("out", "outway"),
    ],
)
def test_vowel_initial_default_suffix(input_text, expected_output):
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


@pytest.mark.parametrize(
    "suffix,expected_output",
    [
        ("way", "appleway"),
        ("yay", "appleyay"),
        ("ay", "appleay"),
    ],
)
def test_vowel_initial_configurable_suffix(suffix, expected_output):
    converter = PigLatinConverter(vowel_initial_suffix=suffix)
    result = asyncio.run(converter.convert_async(prompt="apple"))
    assert result.output_text == expected_output


def test_invalid_suffix_raises():
    with pytest.raises(ValueError):
        PigLatinConverter(vowel_initial_suffix="oink")


@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        # Leading 'y' acts as a consonant and can lead the cluster.
        ("yellow", "ellowyay"),
        # Non-initial 'y' acts as a vowel and terminates the consonant cluster.
        ("my", "ymay"),
        ("rhythm", "ythmrhay"),
        ("sky", "yskay"),
    ],
)
def test_y_handling(input_text, expected_output):
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        # Words with no vowel keep their whole core as the cluster.
        ("nth", "nthay"),
        ("shh", "shhay"),
    ],
)
def test_no_vowel_words(input_text, expected_output):
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        # Trailing punctuation stays attached after the word.
        ("hello,", "ellohay,"),
        ("hello.", "ellohay."),
        ("hello!", "ellohay!"),
        # Leading and trailing punctuation both preserved.
        ("(hello)", "(ellohay)"),
        ('"apple"', '"appleway"'),
        # Interior apostrophe is kept inside the core.
        ("it's", "it'sway"),
    ],
)
def test_punctuation_preserved(input_text, expected_output):
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        # Title case -> capitalized result.
        ("Hello", "Ellohay"),
        ("Apple", "Appleway"),
        # All uppercase (more than one letter) -> uppercase result.
        ("HELLO", "ELLOHAY"),
        ("APPLE", "APPLEWAY"),
        # Lowercase stays lowercase.
        ("hello", "ellohay"),
    ],
)
def test_capitalization(input_text, expected_output):
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


def test_sentence_preserves_structure():
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt="The quick brown fox."))
    # "The" -> title-cased; punctuation attached to "fox".
    assert result.output_text == "Ethay uickqay ownbray oxfay."


@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        # Tabs and newlines are word boundaries too (not only spaces); every word is converted.
        ("hello\tworld", "ellohay orldway"),
        ("hello\nworld", "ellohay orldway"),
        ("one two\tthree\nfour", "oneway otway eethray ourfay"),
    ],
)
def test_splits_on_all_whitespace(input_text, expected_output):
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


def test_punctuation_heavy_token_is_linear_and_correct():
    # A long interior punctuation run must not trigger catastrophic regex backtracking; the linear
    # affix scan returns promptly. The core runs first-letter..last-letter (interior '!' kept), and
    # 'a' is vowel-initial, so "way" is appended.
    token = "a" + "!" * 10000 + "b"
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=token))
    assert result.output_text == token + "way"


@pytest.mark.parametrize(
    "input_text",
    [
        # Tokens with no letters are returned unchanged.
        "123",
        "!!!",
        "...",
    ],
)
def test_non_letter_tokens_unchanged(input_text):
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == input_text


def test_empty_string():
    converter = PigLatinConverter()
    result = asyncio.run(converter.convert_async(prompt=""))
    assert result.output_text == ""


def test_unsupported_input_type_raises():
    converter = PigLatinConverter()
    with pytest.raises(ValueError):
        asyncio.run(converter.convert_async(prompt="hello", input_type="image_path"))
