# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import random
from collections.abc import Callable

import pytest

from pyrit.common.random_context import configure_random_seed
from pyrit.converter import (
    AnsiAttackConverter,
    AsciiArtConverter,
    AskToDecodeConverter,
    CharSwapConverter,
    ColloquialWordswapConverter,
    Converter,
    ConverterResult,
    EmojiConverter,
    InsertPunctuationConverter,
    LeetspeakConverter,
    MathObfuscationConverter,
    RandomCapitalLettersConverter,
    SearchReplaceConverter,
    TemplateSegmentConverter,
    UnicodeConfusableConverter,
    WordProportionSelectionStrategy,
    ZalgoConverter,
)
from pyrit.models import PromptDataType


def _stochastic_converter_cases() -> list[tuple[Callable[[], Converter], str]]:
    return [
        (AskToDecodeConverter, "encoded text"),
        (lambda: AsciiArtConverter(font="rand"), "random font"),
        (lambda: RandomCapitalLettersConverter(percentage=50.0), "abcdefghijklmnopqrstuvwxyz"),
        (EmojiConverter, "deterministic emoji output"),
        (
            lambda: CharSwapConverter(
                max_iterations=3,
                word_selection_strategy=WordProportionSelectionStrategy(proportion=0.8),
            ),
            "deterministic character swapping output",
        ),
        (lambda: InsertPunctuationConverter(word_swap_ratio=0.5), "deterministic punctuation output"),
        (AnsiAttackConverter, "deterministic ANSI output"),
        (TemplateSegmentConverter, "deterministic template segment output"),
        (
            lambda: ZalgoConverter(
                word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5),
            ),
            "alpha bravo charlie delta",
        ),
        (ColloquialWordswapConverter, "tell me how to cut down a tree"),
        (lambda: LeetspeakConverter(deterministic=False), "tell me how to cut down a tree"),
        (MathObfuscationConverter, "deterministic math output"),
        (lambda: SearchReplaceConverter(pattern="x", replace=["a", "b", "c"]), "xxx"),
        (UnicodeConfusableConverter, "deterministic confusable output"),
    ]


def teardown_function() -> None:
    """Restore unseeded behavior after each test."""
    configure_random_seed(seed=None)


class _NestedRandomConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, child: Converter | None = None) -> None:
        self._child = child
        self.draws: tuple[float, float] | None = None

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        rng = self._get_random_generator(stream="values")
        first = rng.random()
        if self._child:
            await self._child.convert_async(prompt=prompt, input_type=input_type)
        second = rng.random()
        self.draws = (first, second)
        return ConverterResult(output_text=prompt, output_type="text")


@pytest.mark.parametrize(("converter_factory", "prompt"), _stochastic_converter_cases())
async def test_initialized_seed_makes_converter_repeatable(
    converter_factory: Callable[[], Converter],
    prompt: str,
) -> None:
    configure_random_seed(seed=42)
    converter = converter_factory()

    first = await converter.convert_async(prompt=prompt)
    second = await converter.convert_async(prompt=prompt)

    assert first == second


@pytest.mark.parametrize(("converter_factory", "prompt"), _stochastic_converter_cases())
async def test_initialized_seed_does_not_disturb_global_rng(
    converter_factory: Callable[[], Converter],
    prompt: str,
) -> None:
    original_state = random.getstate()
    try:
        random.seed(0)
        state_before = random.getstate()
        configure_random_seed(seed=42)

        await converter_factory().convert_async(prompt=prompt)

        assert random.getstate() == state_before
    finally:
        random.setstate(original_state)


async def test_initialized_seed_is_parallel_order_independent() -> None:
    configure_random_seed(seed=42)
    converter = ZalgoConverter(
        word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5),
    )
    prompts = ["alpha bravo charlie delta", "echo foxtrot golf hotel"]

    forward = await asyncio.gather(*(converter.convert_async(prompt=prompt) for prompt in prompts))
    reverse = await asyncio.gather(*(converter.convert_async(prompt=prompt) for prompt in reversed(prompts)))

    assert [result.output_text for result in forward] == [result.output_text for result in reversed(reverse)]


async def test_explicit_converter_seed_overrides_initialized_seed() -> None:
    converter = ZalgoConverter(
        seed=7,
        word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5),
    )
    prompt = "alpha bravo charlie delta echo foxtrot golf hotel"

    configure_random_seed(seed=1)
    first = await converter.convert_async(prompt=prompt)
    configure_random_seed(seed=99)
    second = await converter.convert_async(prompt=prompt)

    assert first == second


async def test_nested_same_class_converter_uses_independent_stream() -> None:
    configure_random_seed(seed=42)
    standalone = _NestedRandomConverter()
    nested = _NestedRandomConverter(child=_NestedRandomConverter())

    await standalone.convert_async(prompt="test")
    await nested.convert_async(prompt="test")

    assert nested.draws == standalone.draws
