# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import random
import threading
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


class _ParallelRandomConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self) -> None:
        self.task_generators: tuple[random.Random, random.Random] | None = None
        self.task_draws: tuple[float, float] | None = None

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        async def draw_async(*, delay: float) -> tuple[random.Random, float]:
            await asyncio.sleep(delay)
            rng = self._get_random_generator(stream="values")
            return (rng, rng.random())

        first, second = await asyncio.gather(
            draw_async(delay=0.01),
            draw_async(delay=0),
        )
        self.task_generators = (first[0], second[0])
        self.task_draws = (first[1], second[1])
        return ConverterResult(output_text=prompt, output_type="text")


class _ParallelThreadRandomConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self) -> None:
        self.thread_generators: tuple[random.Random, random.Random] | None = None
        self.thread_draws: tuple[float, float] | None = None

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        barrier = threading.Barrier(2)

        def draw() -> tuple[random.Random, float]:
            barrier.wait(timeout=5)
            rng = self._get_random_generator(stream="values")
            return (rng, rng.random())

        first, second = await asyncio.gather(
            asyncio.to_thread(draw),
            asyncio.to_thread(draw),
        )
        self.thread_generators = (first[0], second[0])
        self.thread_draws = (first[1], second[1])
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


async def test_initialized_seed_diversifies_distinct_inputs() -> None:
    configure_random_seed(seed=42)
    converter = _NestedRandomConverter()

    await converter.convert_async(prompt="first")
    first_draws = converter.draws
    await converter.convert_async(prompt="second")

    assert converter.draws != first_draws


async def test_child_tasks_do_not_share_mutable_generator() -> None:
    configure_random_seed(seed=42)
    converter = _ParallelRandomConverter()

    await converter.convert_async(prompt="test")

    assert converter.task_generators is not None
    assert converter.task_generators[0] is not converter.task_generators[1]
    assert converter.task_draws is not None
    assert converter.task_draws[0] == converter.task_draws[1]


async def test_worker_threads_do_not_share_mutable_generator() -> None:
    configure_random_seed(seed=42)
    converter = _ParallelThreadRandomConverter()

    await converter.convert_async(prompt="test")

    assert converter.thread_generators is not None
    assert converter.thread_generators[0] is not converter.thread_generators[1]
    assert converter.thread_draws is not None
    assert converter.thread_draws[0] == converter.thread_draws[1]
