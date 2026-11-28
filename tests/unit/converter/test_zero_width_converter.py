# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, ZeroWidthConverter


async def test_convert_async_injects_zero_width_spaces():
    converter = ZeroWidthConverter()
    text = "Hello"
    expected_output = "H\u200be\u200bl\u200bl\u200bo"  # Zero-width spaces between each character
    result = await converter.convert_async(prompt=text)
    assert isinstance(result, ConverterResult)
    assert result.output_text == expected_output  # Check if output matches expected result with zero-width spaces


async def test_convert_async_long_text():
    converter = ZeroWidthConverter()
    text = "This is a longer text used to test the ZeroWidthConverter."
    # Expected output has a zero-width space between every character in `text`
    expected_output = "\u200b".join(text)

    result = await converter.convert_async(prompt=text)

    assert result.output_text == expected_output
    # Verify that the output length is as expected: original length + (number of characters - 1) for zero-width spaces
    assert len(result.output_text) == len(text) + len(text) - 1


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("cafe\u0301", "c\u200ba\u200bf\u200be\u0301"),
        ("\u0939\u093f\u0902\u0926\u0940", "\u0939\u093f\u0902\u200b\u0926\u0940"),
        ("\U0001f1ee\U0001f1f3\U0001f44d\U0001f3fd", "\U0001f1ee\U0001f1f3\u200b\U0001f44d\U0001f3fd"),
        ("\U0001f468\u200d\U0001f469\u200d\U0001f467ok", "\U0001f468\u200d\U0001f469\u200d\U0001f467\u200bo\u200bk"),
        ("a\r\nb", "a\u200b\r\n\u200bb"),
    ],
)
async def test_convert_async_keeps_grapheme_clusters(*, prompt: str, expected: str) -> None:
    result = await ZeroWidthConverter().convert_async(prompt=prompt)

    assert result.output_text == expected


async def test_convert_async_handles_empty_string():
    converter = ZeroWidthConverter()
    text = ""
    result = await converter.convert_async(prompt=text)
    assert result.output_text == ""  # Output should be empty if input is empty


async def test_convert_async_non_text_input_type():
    converter = ZeroWidthConverter()
    with pytest.raises(ValueError) as excinfo:
        await converter.convert_async(prompt="Hello", input_type="non-text")
    assert "Only 'text' input type is supported" in str(excinfo.value)


def test_input_supported_text():
    converter = ZeroWidthConverter()
    assert converter.input_supported("text") is True  # Should support 'text' input type


def test_input_supported_non_text():
    converter = ZeroWidthConverter()
    assert converter.input_supported("non-text") is False  # Should not support non-'text' input types


async def test_convert_async_single_character():
    converter = ZeroWidthConverter()
    text = "A"  # Single character input
    expected_output = "A"  # Should remain unchanged without zero-width spaces
    result = await converter.convert_async(prompt=text)
    assert result.output_text == expected_output


async def test_convert_async_multiple_whitespace():
    converter = ZeroWidthConverter()
    text = "   "  # Input with multiple whitespace characters

    # Converter Behavior: The ZeroWidthConverter inserts zero-width spaces between each character,
    # resulting in N - 1 zero-width spaces for an input of length N.
    # For three spaces, there will be two zero-width spaces between them.
    expected_output = " \u200b \u200b "

    result = await converter.convert_async(prompt=text)
    assert result.output_text == expected_output, f"Unexpected output: {result.output_text}"


_ZERO_WIDTH = (
    ZeroWidthConverter.ZERO_WIDTH_SPACE,
    ZeroWidthConverter.ZERO_WIDTH_NON_JOINER,
    ZeroWidthConverter.ZERO_WIDTH_JOINER,
)


def _strip_zero_width(text: str) -> str:
    return "".join(character for character in text if character not in _ZERO_WIDTH)


def _count_zero_width(text: str) -> int:
    return sum(character in _ZERO_WIDTH for character in text)


@pytest.mark.parametrize("budget", [1, 2, 5])
async def test_convert_async_budget_inserts_exactly_budget_characters(budget: int) -> None:
    converter = ZeroWidthConverter(budget=budget, seed=0)

    result = await converter.convert_async(prompt="Hello world")

    assert _count_zero_width(result.output_text) == budget
    assert _strip_zero_width(result.output_text) == "Hello world"


async def test_convert_async_budget_larger_than_boundaries_fills_every_boundary() -> None:
    converter = ZeroWidthConverter(budget=10, seed=0)

    result = await converter.convert_async(prompt="abc")

    assert result.output_text == "\u200ba\u200bb\u200bc\u200b"


async def test_convert_async_budget_keeps_grapheme_clusters() -> None:
    prompt = "\U0001f468\u200d\U0001f469\u200d\U0001f467e\u0301"
    converter = ZeroWidthConverter(budget=3, seed=0)

    result = await converter.convert_async(prompt=prompt)

    assert result.output_text == "\u200b\U0001f468\u200d\U0001f469\u200d\U0001f467\u200be\u0301\u200b"


async def test_convert_async_budget_empty_prompt_stays_empty() -> None:
    result = await ZeroWidthConverter(budget=1, seed=0).convert_async(prompt="")

    assert result.output_text == ""


async def test_convert_async_budget_is_reproducible_with_seed() -> None:
    prompt = "The quick brown fox jumps over the lazy dog"
    first = await ZeroWidthConverter(budget=3, characters=_ZERO_WIDTH, seed=7).convert_async(prompt=prompt)
    second = await ZeroWidthConverter(budget=3, characters=_ZERO_WIDTH, seed=7).convert_async(prompt=prompt)

    assert first.output_text == second.output_text


async def test_convert_async_budget_different_seeds_change_positions() -> None:
    prompt = "The quick brown fox jumps over the lazy dog"
    outputs = {
        (await ZeroWidthConverter(budget=1, seed=seed).convert_async(prompt=prompt)).output_text for seed in range(10)
    }

    assert len(outputs) > 1


async def test_convert_async_budget_uses_only_given_characters() -> None:
    converter = ZeroWidthConverter(budget=20, characters=[ZeroWidthConverter.ZERO_WIDTH_JOINER], seed=0)

    result = await converter.convert_async(prompt="Hello world")

    assert ZeroWidthConverter.ZERO_WIDTH_SPACE not in result.output_text
    assert result.output_text.count(ZeroWidthConverter.ZERO_WIDTH_JOINER) == len("Hello world") + 1


async def test_convert_async_characters_without_budget_fills_every_gap() -> None:
    converter = ZeroWidthConverter(characters=_ZERO_WIDTH, seed=0)

    result = await converter.convert_async(prompt="Hello")

    assert _count_zero_width(result.output_text) == 4
    assert _strip_zero_width(result.output_text) == "Hello"
    assert result.output_text[0] == "H"
    assert result.output_text[-1] == "o"


@pytest.mark.parametrize("budget", [0, -1, True])
def test_init_rejects_invalid_budget(budget: int) -> None:
    with pytest.raises(ValueError, match="budget must be a positive integer"):
        ZeroWidthConverter(budget=budget)


@pytest.mark.parametrize("characters", [[], [""], ["\u200b", ""]])
def test_init_rejects_invalid_characters(characters: list[str]) -> None:
    with pytest.raises(ValueError, match="characters must be a non-empty sequence"):
        ZeroWidthConverter(characters=characters)


def test_default_identifier_has_no_new_params() -> None:
    params = ZeroWidthConverter().get_identifier().params

    assert "budget" not in params
    assert "characters" not in params
    assert "seed" not in params


def test_identifier_includes_budget_characters_and_seed() -> None:
    params = ZeroWidthConverter(budget=2, characters=_ZERO_WIDTH, seed=3).get_identifier().params

    assert params["budget"] == 2
    assert params["characters"] == list(_ZERO_WIDTH)
    assert params["seed"] == 3
