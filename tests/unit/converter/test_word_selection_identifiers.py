# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Callable, Iterator
from functools import partial

import pytest

from pyrit.common.random_context import configure_random_seed, get_configured_random_seed
from pyrit.converter import (
    AllWordsSelectionStrategy,
    CharSwapConverter,
    FirstLetterConverter,
    LeetspeakConverter,
    UnicodeReplacementConverter,
    WordIndexSelectionStrategy,
    WordKeywordSelectionStrategy,
    WordProportionSelectionStrategy,
    WordSelectionStrategy,
    ZalgoConverter,
)
from pyrit.converter.word_level_converter import WordLevelConverter
from pyrit.registry import ConverterRegistry


@pytest.fixture(
    params=[CharSwapConverter, FirstLetterConverter, LeetspeakConverter, UnicodeReplacementConverter, ZalgoConverter],
    ids=lambda converter: converter.__name__,
)
def converter_type(request: pytest.FixtureRequest) -> type[WordLevelConverter]:
    converter = request.param
    assert isinstance(converter, type)
    assert issubclass(converter, WordLevelConverter)
    return converter


@pytest.fixture
def seeded_randomness() -> Iterator[None]:
    previous_seed = get_configured_random_seed()
    configure_random_seed(seed=123)
    try:
        yield
    finally:
        configure_random_seed(seed=previous_seed)


@pytest.mark.parametrize(
    ("first_strategy", "second_strategy"),
    [
        (WordIndexSelectionStrategy(indices=[0]), WordIndexSelectionStrategy(indices=[1])),
        (WordKeywordSelectionStrategy(keywords=["alpha"]), WordKeywordSelectionStrategy(keywords=["beta"])),
    ],
    ids=["indices", "keywords"],
)
async def test_identifier_distinguishes_selected_words_async(
    *,
    converter_type: type[WordLevelConverter],
    first_strategy: WordSelectionStrategy,
    second_strategy: WordSelectionStrategy,
) -> None:
    kwargs = {"max_iterations": 1} if converter_type is CharSwapConverter else {}
    first = converter_type(word_selection_strategy=first_strategy, **kwargs)
    second = converter_type(word_selection_strategy=second_strategy, **kwargs)

    first_result = await first.convert_async(prompt="alpha beta")
    second_result = await second.convert_async(prompt="alpha beta")

    assert first_result.output_text.endswith(" beta")
    assert second_result.output_text.startswith("alpha ")
    assert first_result.output_text != second_result.output_text
    assert first.get_identifier().hash != second.get_identifier().hash

    instances = ConverterRegistry().instances
    instances.register(first)
    instances.register(second)
    assert instances.get(first.get_identifier().unique_name) is first
    assert instances.get(second.get_identifier().unique_name) is second


@pytest.mark.parametrize(
    ("first_strategy", "second_strategy"),
    [
        (WordProportionSelectionStrategy(proportion=0.2), WordProportionSelectionStrategy(proportion=0.5)),
        (WordProportionSelectionStrategy(proportion=0.2), WordProportionSelectionStrategy(proportion=0.2, seed=7)),
        (
            WordKeywordSelectionStrategy(keywords=["alpha"], case_sensitive=True),
            WordKeywordSelectionStrategy(keywords=["alpha"], case_sensitive=False),
        ),
    ],
    ids=["proportion", "selection-seed", "case-sensitivity"],
)
def test_identifier_distinguishes_selection_settings(
    *,
    converter_type: type[WordLevelConverter],
    first_strategy: WordSelectionStrategy,
    second_strategy: WordSelectionStrategy,
) -> None:
    first = converter_type(word_selection_strategy=first_strategy)
    second = converter_type(word_selection_strategy=second_strategy)

    assert first.get_identifier().hash != second.get_identifier().hash


def test_identifier_preserves_index_order(converter_type: type[WordLevelConverter]) -> None:
    first = converter_type(word_selection_strategy=WordIndexSelectionStrategy(indices=[0, 1]))
    second = converter_type(word_selection_strategy=WordIndexSelectionStrategy(indices=[1, 0]))

    assert first.get_identifier().hash != second.get_identifier().hash


@pytest.mark.usefixtures("seeded_randomness")
@pytest.mark.parametrize(
    "converter_factory",
    [
        pytest.param(partial(CharSwapConverter, max_iterations=1, seed=123), id="charswap"),
        pytest.param(partial(ZalgoConverter, seed=123), id="zalgo"),
        pytest.param(partial(LeetspeakConverter, deterministic=False), id="leetspeak"),
    ],
)
async def test_reordered_indices_distinguish_stochastic_outputs_async(
    converter_factory: Callable[..., WordLevelConverter],
) -> None:
    first = converter_factory(word_selection_strategy=WordIndexSelectionStrategy(indices=[0, 1]))
    second = converter_factory(word_selection_strategy=WordIndexSelectionStrategy(indices=[1, 0]))

    first_result = await first.convert_async(prompt="alpha beta")
    second_result = await second.convert_async(prompt="alpha beta")
    assert (await first.convert_async(prompt="alpha beta")).output_text == first_result.output_text
    assert (await second.convert_async(prompt="alpha beta")).output_text == second_result.output_text
    assert first_result.output_text != second_result.output_text
    assert first.get_identifier().hash != second.get_identifier().hash

    instances = ConverterRegistry().instances
    instances.register(first)
    instances.register(second)
    assert instances.get(first.get_identifier().unique_name) is first
    assert instances.get(second.get_identifier().unique_name) is second


@pytest.mark.parametrize("explicit_default", [False, True])
def test_identifier_preserves_default_hash(*, converter_type: type[WordLevelConverter], explicit_default: bool) -> None:
    expected_hashes = {
        CharSwapConverter: "826479a529db24708e0711f8d7fd6a3b79b82b1848e85e8b039933b0c2d19d4d",
        FirstLetterConverter: "a0268c3263b42bad4bf082b273ac86180babd698cdd2bffcd5d9a3da26da0dae",
        LeetspeakConverter: "ff4bc9afc79df2afbffd9d5bfe08f133339f8ec2f56e08d0fd3ee6f6af5b87cc",
        UnicodeReplacementConverter: "173af73cd1eb54bcfaffb5dcc4eee0801325c36d73e4295c1e184b3fd1ec0e5c",
        ZalgoConverter: "78d075f9172fd497bf741ddf148a6d43b4f4a9e31b59d21e692ab34bf1076f4e",
    }
    default_strategy = (
        WordProportionSelectionStrategy(proportion=0.2)
        if converter_type is CharSwapConverter
        else AllWordsSelectionStrategy()
    )
    converter = converter_type(word_selection_strategy=default_strategy if explicit_default else None)

    assert converter.get_identifier().hash == expected_hashes[converter_type]
