# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.prompt_converter import InsertPunctuationConverter


# Test for correctness
# Long prompt, short prompt, weird spacing and punctuation, non-wordy prompt, short and empty prompt
@pytest.mark.parametrize(
    "input_prompt,between_words,punctuation_list,word_swap_ratio,expected_punctuation_count",
    [
        ("Despite the rain, we decided to go hiking; it was a refreshing experience.", True, [",", "!", "]"], 1, 16),
        ("Quick!", False, [",", "~", "]"], 1, 2),
        ("   Hello,   world!   ", True, [",", "[", "]"], 0.3, 3),
        ("....", True, [",", "[", ">"], 0.2, 5),
        ("Numbers are also words, 1234 not intuitive, not symbols $@.", True, [",", "[", "]"], 0.6, 10),
        ("", True, [",", "$", "]"], 0.9, 1),
        ("a b", False, [",", "^", "]"], 1, 2),
        ("I can't wait!!!", False, [",", "/", "]"], 0.4, 6),
    ],
)
@pytest.mark.asyncio
async def test_word_swap_ratio(
    input_prompt, between_words, punctuation_list, word_swap_ratio, expected_punctuation_count
):
    converter = InsertPunctuationConverter(word_swap_ratio=word_swap_ratio, between_words=between_words)
    result = await converter.convert_async(prompt=input_prompt, punctuation_list=punctuation_list)
    modified_prompt = result.output_text
    assert (punctuation_count := len(re.findall(r"[^\w\s]", modified_prompt))) == expected_punctuation_count, (
        f"Expect {expected_punctuation_count} punctuations found in prompt: {punctuation_count}"
    )


# Test default swap ratio = 0.2
@pytest.mark.parametrize(
    "input_prompt, expected_punctuation_count",
    [("count 1 2 3 4 5 6 7 8 9 and 10.", 3), ("Aha!", 2)],
)
@pytest.mark.asyncio
async def test_default_swap(input_prompt, expected_punctuation_count):
    converter = InsertPunctuationConverter()
    result = await converter.convert_async(prompt=input_prompt)
    modified_prompt = result.output_text
    assert (punctuation_count := len(re.findall(r"[^\w\s]", modified_prompt))) == expected_punctuation_count, (
        f"Expect {expected_punctuation_count} punctuations found in prompt: {punctuation_count}"
    )


# Test value error raising for invalid swap ratio
@pytest.mark.parametrize(
    "word_swap_ratio",
    [-0.1, 1.5],
)
@pytest.mark.asyncio
async def test_invalid_word_swap_ratio(word_swap_ratio):
    with pytest.raises(ValueError):
        InsertPunctuationConverter(word_swap_ratio=word_swap_ratio)


# Test value error raising for invalid punctuations
@pytest.mark.parametrize(
    "punctuation_list",
    ["~~", " ", "1", "a", "//"],
)
@pytest.mark.asyncio
async def test_invalid_punctuation_list(punctuation_list):
    with pytest.raises(ValueError):
        converter = InsertPunctuationConverter()
        await converter.convert_async(prompt="prompt", punctuation_list=[punctuation_list])
