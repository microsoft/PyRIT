# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio

import pytest

from pyrit.prompt_converter.leetspeak_converter import LeetspeakConverter


# Test for deterministic mode
@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        ("leet", "1337"),  # "l" -> "1", "e" -> "3", "t" -> "7"
        ("hello", "h3110"),  # "h" -> "h", "e" -> "3", "l" -> "1", "o" -> "0"
        ("LeetSpeak", "13375p34k"),  # Mixed case, default subs: "L" -> "1", "S" -> "5", etc.
    ],
)
def test_leetspeak_deterministic(input_text, expected_output):
    converter = LeetspeakConverter(deterministic=True)
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


# Test for non-deterministic mode
@pytest.mark.parametrize(
    "input_text",
    [
        "code",  # A different input set to reduce redundancy
        "testing",
        "pyrit",
    ],
)
def test_leetspeak_non_deterministic(input_text):
    converter = LeetspeakConverter(deterministic=False)
    result = asyncio.run(converter.convert_async(prompt=input_text))

    # Default substitution mappings
    valid_chars = {
        "a": ["4", "@", "/\\", "@", "^", "/-\\"],
        "b": ["8", "6", "13", "|3", "/3", "!3"],
        "c": ["(", "[", "<", "{"],
        "e": ["3"],
        "g": ["9"],
        "i": ["1", "!"],
        "l": ["1", "|"],
        "o": ["0"],
        "s": ["5", "$"],
        "t": ["7"],
        "z": ["2"],
        "L": ["1", "|"],
        "E": ["3"],
        "T": ["7"],
        "H": ["#", "H"],
        "O": ["0"],
        "S": ["5", "$"],
        "A": ["4", "@", "/\\", "^", "/-\\"],
        "B": ["8", "6", "13", "|3", "/3", "!3"],
        "C": ["(", "[", "<", "{"],
        "G": ["9"],
        "I": ["1", "!"],
        "Z": ["2"],
    }

    # Check that each character in the output is a valid substitution
    assert all(
        char in valid_chars.get(original_char, [original_char])
        for original_char, char in zip(input_text, result.output_text, strict=False)
    )


# Test for custom substitutions
@pytest.mark.parametrize(
    "input_text,custom_substitutions,expected_output",
    [
        ("leet", {"l": ["7"], "e": ["€"], "t": ["+"]}, "7€€+"),  # Custom substitution test
    ],
)
def test_leetspeak_custom_substitutions(input_text, custom_substitutions, expected_output):
    converter = LeetspeakConverter(deterministic=True, custom_substitutions=custom_substitutions)
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


# Test for empty custom substitutions
@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        ("leet", "1337"),  # Using default substitutions when custom is empty
    ],
)
def test_leetspeak_empty_custom_substitutions(input_text, expected_output):
    converter = LeetspeakConverter(deterministic=True, custom_substitutions={})
    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


def test_leetspeak_input_supported():
    converter = LeetspeakConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("audio_path") is False
