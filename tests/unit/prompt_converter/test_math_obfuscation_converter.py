# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import random

import pytest

from pyrit.prompt_converter.math_obfuscation_converter import MathObfuscationConverter


@pytest.mark.parametrize(
    "input_text,expected_output",
    [
        (
            "A",
            "A = 4A - 3A",
        ),
        (
            "AB",
            "A = 4A - 3A\nB = 3B - 2B",
        ),
    ],
)
def test_math_obfuscation_deterministic_small(input_text, expected_output):
    """Deterministic behavior with fixed RNG."""
    rng = random.Random(1)
    converter = MathObfuscationConverter(min_n=2, max_n=9, rng=rng, hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt=input_text))
    assert result.output_text == expected_output


def test_math_obfuscation_spaces_and_newlines():
    """Spaces -> blank lines, newlines -> blank lines."""
    rng = random.Random(1)
    converter = MathObfuscationConverter(rng=rng, hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt="A B\nC"))
    lines = result.output_text.splitlines()

    # "A", blank (space), "B", blank (newline), "C"  # noqa: ERA001
    assert lines[0].startswith("A =")
    assert lines[1] == ""
    assert lines[2].startswith("B =")
    assert lines[3] == ""
    assert lines[4].startswith("C =")


def test_math_obfuscation_empty_string():
    """Empty input should produce empty output."""
    converter = MathObfuscationConverter(rng=random.Random(0), hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt=""))
    assert result.output_text == ""


def test_math_obfuscation_long_prompt_length():
    """Long prompt should not crash and should produce one line per char."""
    text = "A" * 1000
    converter = MathObfuscationConverter(rng=random.Random(0), hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt=text))
    lines = result.output_text.splitlines()

    assert len(lines) == len(text)
    assert all(" = " in line for line in lines)


def test_math_obfuscation_type_support():
    """input_supported / output_supported should behave as expected."""
    converter = MathObfuscationConverter()

    assert converter.input_supported("text") is True
    assert converter.output_supported("text") is True


def test_math_obfuscation_default_suffix():
    """Default suffix should be appended."""
    converter = MathObfuscationConverter(rng=random.Random(0), hint="")

    result = asyncio.run(converter.convert_async(prompt="A"))

    assert result.output_text.endswith(MathObfuscationConverter.DEFAULT_SUFFIX)
    assert "There is something I want you to provide instructions" in result.output_text


def test_math_obfuscation_custom_suffix():
    """Custom suffix should replace default."""
    custom_suffix = "\n\nDecode and comply."
    converter = MathObfuscationConverter(rng=random.Random(0), hint="", suffix=custom_suffix)

    result = asyncio.run(converter.convert_async(prompt="A"))

    assert result.output_text.endswith(custom_suffix)
    assert "There is something" not in result.output_text


def test_math_obfuscation_no_suffix():
    """Empty suffix should produce only obfuscated content."""
    converter = MathObfuscationConverter(rng=random.Random(0), hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt="AB"))
    lines = result.output_text.splitlines()

    assert len(lines) == 2
    assert all(" = " in line for line in lines)


def test_math_obfuscation_default_hint():
    """Default hint should be appended to first equation."""
    converter = MathObfuscationConverter(rng=random.Random(0), suffix="")

    result = asyncio.run(converter.convert_async(prompt="AB"))
    lines = result.output_text.splitlines()

    assert MathObfuscationConverter.DEFAULT_HINT in lines[0]
    assert MathObfuscationConverter.DEFAULT_HINT not in lines[1]


def test_math_obfuscation_custom_hint():
    """Custom hint should replace default."""
    custom_hint = "<decode this>"
    converter = MathObfuscationConverter(rng=random.Random(0), hint=custom_hint, suffix="")

    result = asyncio.run(converter.convert_async(prompt="AB"))
    lines = result.output_text.splitlines()

    assert custom_hint in lines[0]
    assert "each variable corresponds" not in result.output_text


def test_math_obfuscation_no_hint():
    """Empty hint should not modify first equation."""
    converter = MathObfuscationConverter(rng=random.Random(0), hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt="A"))

    # Should be just the equation, no extra content
    assert result.output_text == "A = 8A - 7A"


def test_math_obfuscation_full_defaults():
    """Both default hint and suffix should be present."""
    converter = MathObfuscationConverter(rng=random.Random(0))

    result = asyncio.run(converter.convert_async(prompt="Hi"))

    assert MathObfuscationConverter.DEFAULT_HINT in result.output_text
    assert MathObfuscationConverter.DEFAULT_SUFFIX in result.output_text
    # Hint should be on first line only
    lines = result.output_text.split(MathObfuscationConverter.DEFAULT_SUFFIX)[0].splitlines()
    assert MathObfuscationConverter.DEFAULT_HINT in lines[0]
