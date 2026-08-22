# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random
from collections.abc import Callable

import pytest

from pyrit.converter import (
    AnsiAttackConverter,
    AskToDecodeConverter,
    CharSwapConverter,
    Converter,
    EmojiConverter,
    InsertPunctuationConverter,
    RandomCapitalLettersConverter,
    TemplateSegmentConverter,
    WordProportionSelectionStrategy,
)


def _seeded_converter_cases() -> list[tuple[Callable[[], Converter], str]]:
    return [
        (lambda: AskToDecodeConverter(seed=42), "encoded text"),
        (lambda: RandomCapitalLettersConverter(percentage=50.0, seed=42), "abcdefghijklmnopqrstuvwxyz"),
        (lambda: EmojiConverter(seed=42), "deterministic emoji output"),
        (
            lambda: CharSwapConverter(
                max_iterations=3,
                seed=42,
                word_selection_strategy=WordProportionSelectionStrategy(proportion=0.8, seed=42),
            ),
            "deterministic character swapping output",
        ),
        (lambda: InsertPunctuationConverter(word_swap_ratio=0.5, seed=42), "deterministic punctuation output"),
        (lambda: AnsiAttackConverter(seed=42), "deterministic ANSI output"),
        (lambda: TemplateSegmentConverter(seed=42), "deterministic template segment output"),
    ]


@pytest.mark.parametrize(("converter_factory", "prompt"), _seeded_converter_cases())
async def test_seeded_converter_is_repeatable(
    converter_factory: Callable[[], Converter],
    prompt: str,
) -> None:
    converter = converter_factory()

    first = await converter.convert_async(prompt=prompt)
    second = await converter.convert_async(prompt=prompt)

    assert first == second


@pytest.mark.parametrize(("converter_factory", "prompt"), _seeded_converter_cases())
async def test_seeded_converter_does_not_disturb_global_rng(
    converter_factory: Callable[[], Converter],
    prompt: str,
) -> None:
    original_state = random.getstate()
    try:
        random.seed(0)
        state_before = random.getstate()

        await converter_factory().convert_async(prompt=prompt)

        assert random.getstate() == state_before
    finally:
        random.setstate(original_state)
