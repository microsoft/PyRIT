# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import random
import re

import pytest

from pyrit.converter import MathObfuscationConverter
from pyrit.registry import ConverterRegistry


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


@pytest.mark.parametrize("digit", list("0123456789") + ["\u0665"])
def test_math_obfuscation_digit_lines_are_true_identities(digit):
    converter = MathObfuscationConverter(min_n=2, max_n=9, rng=random.Random(3), hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt=digit * 20))

    for line in result.output_text.splitlines():
        match = re.fullmatch(rf"{digit} = (\d)\*{digit} - (\d)\*{digit}", line)
        assert match is not None, line
        value = int(digit)
        assert int(match.group(1)) * value - int(match.group(2)) * value == value
        assert int(match.group(1)) - int(match.group(2)) == 1


@pytest.mark.parametrize("character", ["\u00b2", "\u2083", "\u00bd", "\u2167", "\u4e94"])
def test_math_obfuscation_other_numeric_characters_use_explicit_multiplication(character):
    converter = MathObfuscationConverter(min_n=2, max_n=9, rng=random.Random(3), hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt=character * 20))

    for line in result.output_text.splitlines():
        match = re.fullmatch(rf"{character} = (\d)\*{character} - (\d)\*{character}", line)
        assert match is not None, line
        assert int(match.group(1)) - int(match.group(2)) == 1


def test_math_obfuscation_digits_and_letters_in_one_prompt():
    converter = MathObfuscationConverter(rng=random.Random(1), hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt="A1"))

    assert result.output_text == "A = 4A - 3A\n1 = 3*1 - 2*1"


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


def test_math_obfuscation_crlf_and_cr():
    """CRLF and CR should be treated as newlines without generating control character equations."""
    converter = MathObfuscationConverter(rng=random.Random(1), hint="", suffix="")

    result_crlf = asyncio.run(converter.convert_async(prompt="A\r\nB"))
    lines_crlf = result_crlf.output_text.splitlines()
    assert lines_crlf[0].startswith("A =")
    assert lines_crlf[1] == ""
    assert lines_crlf[2].startswith("B =")
    assert not any(line.startswith("\r =") for line in lines_crlf)

    result_cr = asyncio.run(converter.convert_async(prompt="A\rB"))
    lines_cr = result_cr.output_text.splitlines()
    assert lines_cr[0].startswith("A =")
    assert lines_cr[1] == ""
    assert lines_cr[2].startswith("B =")
    assert not any(line.startswith("\r =") for line in lines_cr)


def test_math_obfuscation_tabs():
    """Horizontal tabs should be treated as whitespace word separators."""
    converter = MathObfuscationConverter(rng=random.Random(1), hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt="A\tB"))
    lines = result.output_text.splitlines()
    assert lines[0].startswith("A =")
    assert lines[1] == ""
    assert lines[2].startswith("B =")
    assert not any(line.startswith("\t =") for line in lines)


def test_math_obfuscation_mixed_control_whitespace():
    """Mixed tabs, CRLF, and spaces should process cleanly with equations only for letters."""
    converter = MathObfuscationConverter(rng=random.Random(1), hint="", suffix="")

    result = asyncio.run(converter.convert_async(prompt="A\tB\r\nC\rD"))
    lines = result.output_text.splitlines()
    assert len(lines) == 7
    assert lines[0].startswith("A =")
    assert lines[1] == ""
    assert lines[2].startswith("B =")
    assert lines[3] == ""
    assert lines[4].startswith("C =")
    assert lines[5] == ""
    assert lines[6].startswith("D =")
    assert not any(line.startswith(("\r =", "\t =")) for line in lines)


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


@pytest.mark.parametrize(
    "settings",
    [{"hint": "custom hint"}, {"hint": ""}, {"suffix": "custom suffix"}, {"suffix": ""}],
)
def test_math_obfuscation_identifier_distinguishes_hint_and_suffix(settings: dict[str, str]) -> None:
    default = MathObfuscationConverter()
    other = MathObfuscationConverter(**settings)
    assert default.get_identifier().hash != other.get_identifier().hash

    # Both configurations get their own default registry name instead of colliding.
    instances = ConverterRegistry().instances
    instances.register(default)
    instances.register(other)
    assert instances.get(default.get_identifier().unique_name) is default
    assert instances.get(other.get_identifier().unique_name) is other


def test_math_obfuscation_identifier_is_stable_for_equal_settings() -> None:
    assert (
        MathObfuscationConverter(hint="h", suffix="s").get_identifier().hash
        == MathObfuscationConverter(hint="h", suffix="s").get_identifier().hash
    )
    assert (
        MathObfuscationConverter().get_identifier().hash
        == MathObfuscationConverter(
            hint=MathObfuscationConverter.DEFAULT_HINT, suffix=MathObfuscationConverter.DEFAULT_SUFFIX
        )
        .get_identifier()
        .hash
    )
