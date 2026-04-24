# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.prompt_converter.charswap_attack_converter import CharSwapConverter
from pyrit.prompt_converter.text_selection_strategy import (
    WordProportionSelectionStrategy,
)


# Test that the converter produces the expected number of outputs
@pytest.mark.asyncio
async def test_char_swap_converter_output_count():
    converter = CharSwapConverter(max_iterations=5)
    prompt = "This is a test prompt for the char swap converter."
    result = await converter.convert_async(prompt=prompt)
    output_prompts = result.output_text.strip().split("\n")
    assert len(output_prompts) == 1  # Should generate 1 perturbed prompt


# Test that words longer than 3 characters are being perturbed
@pytest.mark.asyncio
async def test_char_swap_converter_word_perturbation():
    converter = CharSwapConverter(
        max_iterations=1, word_selection_strategy=WordProportionSelectionStrategy(proportion=1.0)
    )
    prompt = "Testing"
    with patch("random.randint", return_value=1):  # Force swap at position 1
        result = await converter.convert_async(prompt=prompt)
        output_prompts = result.output_text.strip().split("\n")
        assert output_prompts[0] == "Tseting"  # 'Testing' with 'e' and 's' swapped


# Test that words of length <= 3 are not perturbed
@pytest.mark.parametrize(
    "prompt",
    ["Try or do?", "To be or not to be.", "2b oR n0t 2b"],
)
@pytest.mark.asyncio
async def test_char_swap_converter_short_words(prompt):
    converter = CharSwapConverter(
        max_iterations=1, word_selection_strategy=WordProportionSelectionStrategy(proportion=1.0)
    )
    result = await converter.convert_async(prompt=prompt)
    output_prompts = result.output_text.strip().split("\n")
    # Since all words are <= 3 letters, output should be the same as input
    assert output_prompts[0] == prompt


# Test that punctuation is not perturbed
@pytest.mark.asyncio
async def test_char_swap_converter_punctuation():
    converter = CharSwapConverter(
        max_iterations=1, word_selection_strategy=WordProportionSelectionStrategy(proportion=1.0)
    )
    prompt = "Hello, world!"
    result = await converter.convert_async(prompt=prompt)
    output_prompts = result.output_text.strip().split("\n")
    # Punctuation should not be perturbed
    assert "," in output_prompts[0]
    assert "!" in output_prompts[0]


# Test that input type not supported raises ValueError
@pytest.mark.asyncio
async def test_char_swap_converter_input_type():
    converter = CharSwapConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="Test prompt", input_type="unsupported")


# Test with zero iterations
@pytest.mark.asyncio
async def test_char_swap_converter_zero_iterations():
    with pytest.raises(ValueError, match="max_iterations must be greater than 0"):
        CharSwapConverter(max_iterations=0)


@pytest.mark.asyncio
async def test_char_swap_converter_sample_ratio_other_than_1():
    converter = CharSwapConverter(
        max_iterations=1, word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5)
    )
    prompt = "Testing word swap ratio"
    result = await converter.convert_async(prompt=prompt)
    output_prompts = result.output_text.strip().split("\n")
    assert output_prompts[0] != prompt


# Test that swapping is happening randomly
@pytest.mark.asyncio
async def test_char_swap_converter_random_swapping():
    converter = CharSwapConverter(
        max_iterations=1, word_selection_strategy=WordProportionSelectionStrategy(proportion=1.0)
    )
    prompt = "Character swapping test"

    with patch(
        "random.sample",
        side_effect=[[0, 1, 2]],
    ):
        result1 = await converter.convert_async(prompt=prompt)

    assert prompt != result1.output_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt,max_iterations,mock_positions,expected",
    [
        # Single swap at position 1: Testing -> Tseting
        ("Testing", 1, [1], "Tseting"),
        # Two swaps at same position reverts: Testing -> Tseting -> Testing
        ("Testing", 2, [1, 1], "Testing"),
        # Three swaps at same position: Testing -> Tseting -> Testing -> Tseting
        ("Testing", 3, [1, 1, 1], "Tseting"),
        # Two swaps at different positions: Testing -> Tseting -> Tsetnig
        ("Testing", 2, [1, 4], "Tsetnig"),
        # Single swap at position 2: Testing -> Tetsing
        ("Testing", 1, [2], "Tetsing"),
        # Longer word, single swap: Character -> Cahracter
        ("Character", 1, [1], "Cahracter"),
        # Longer word, two swaps at different positions
        ("Character", 2, [1, 5], "Cahratcer"),
    ],
)
async def test_char_swap_converter_max_iterations_has_effect(prompt, max_iterations, mock_positions, expected):
    """Test that max_iterations parameter affects perturbation behavior."""
    converter = CharSwapConverter(
        max_iterations=max_iterations,
        word_selection_strategy=WordProportionSelectionStrategy(proportion=1.0),
    )

    with patch("random.randint", side_effect=mock_positions):
        result = await converter.convert_async(prompt=prompt)

    assert result.output_text == expected


@pytest.mark.asyncio
async def test_char_swap_converter_proportion_unchanged_with_iterations():
    """Test that max_iterations doesn't affect which words are selected, only how much they're perturbed."""
    prompt = "Testing multiple words here today"

    # 50% proportion should select ~2-3 of the 5 eligible words, regardless of max_iterations
    converter = CharSwapConverter(
        max_iterations=10,
        word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5),
    )

    # Mock random.sample to select exactly 2 words (indices 0 and 2)
    # This simulates the word selection strategy picking "Testing" and "words"
    with patch("random.sample", return_value=[0, 2]) as mock_sample, patch("random.randint", return_value=1):
        result = await converter.convert_async(prompt=prompt)

    # Verify sample was called once (word selection happens once, not per iteration)
    assert mock_sample.call_count == 1

    # "multiple", "here", "today" should be unchanged
    words = result.output_text.split()
    assert words[1] == "multiple"
    assert words[3] == "here"
    assert words[4] == "today"
