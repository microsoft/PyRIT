# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import itertools

import pytest

from pyrit.common.text_helper import graphemes
from pyrit.converter import BidiSwapConverter, ConverterResult
from pyrit.converter.bidi_swap_converter import _is_swappable

_LRO = chr(0x202D)
_RLO = chr(0x202E)
_PDF = chr(0x202C)
_LRI = chr(0x2066)
_PDI = chr(0x2069)


def _swap(first: str, second: str) -> str:
    return f"{_LRO}{_LRI}{_RLO}{_LRI}{second}{_PDI}{_LRI}{first}{_PDI}{_PDF}{_PDI}{_PDF}"


def _strip_controls(text: str) -> str:
    return "".join(character for character in text if character not in BidiSwapConverter.CONTROL_CHARACTERS)


def _swapped_pair_starts(original: str, stripped: str) -> list[int]:
    before = graphemes(original)
    after = graphemes(stripped)
    assert len(before) == len(after)
    starts = []
    index = 0
    while index < len(before):
        if before[index] == after[index]:
            index += 1
            continue
        assert (after[index], after[index + 1]) == (before[index + 1], before[index])
        starts.append(index)
        index += 2
    return starts


async def test_convert_async_two_characters_swaps_the_only_pair() -> None:
    result = await BidiSwapConverter(seed=0).convert_async(prompt="ab")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == _swap("a", "b")


async def test_convert_async_stores_pair_in_reverse_order() -> None:
    result = await BidiSwapConverter(seed=0).convert_async(prompt="ab")

    assert _strip_controls(result.output_text) == "ba"


async def test_convert_async_control_sequence_is_balanced() -> None:
    result = await BidiSwapConverter(budget=3, seed=0).convert_async(prompt="Hello world")

    text = result.output_text
    assert text.count(_LRO) + text.count(_RLO) == text.count(_PDF)
    assert text.count(_LRI) == text.count(_PDI)


@pytest.mark.parametrize("budget", [1, 2, 4])
async def test_convert_async_swaps_budget_non_overlapping_pairs(budget: int) -> None:
    prompt = "The quick brown fox"

    result = await BidiSwapConverter(budget=budget, seed=1).convert_async(prompt=prompt)

    starts = _swapped_pair_starts(prompt, _strip_controls(result.output_text))
    assert result.output_text.count(_RLO) == budget
    # Equal neighbours (such as a doubled letter) swap invisibly, so a pair may show no change once stripped.
    assert len(starts) <= budget
    assert all(second - first >= 2 for first, second in itertools.pairwise(starts))


async def test_convert_async_budget_larger_than_pairs_swaps_every_pair() -> None:
    result = await BidiSwapConverter(budget=10, seed=0).convert_async(prompt="abcde")

    assert result.output_text.count(_RLO) == 2
    assert _strip_controls(result.output_text) in {"badce", "baced", "acbed"}


async def test_convert_async_even_length_with_full_budget_swaps_all_pairs() -> None:
    result = await BidiSwapConverter(budget=2, seed=0).convert_async(prompt="abcd")

    assert result.output_text == _swap("a", "b") + _swap("c", "d")


@pytest.mark.parametrize("prompt", ["", "a"])
async def test_convert_async_too_short_returns_prompt_unchanged(prompt: str) -> None:
    result = await BidiSwapConverter(seed=0).convert_async(prompt=prompt)

    assert result.output_text == prompt


async def test_convert_async_keeps_grapheme_clusters() -> None:
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"

    result = await BidiSwapConverter(seed=0).convert_async(prompt=f"{family}e\u0301")

    assert result.output_text == _swap(family, "e\u0301")


async def test_convert_async_is_reproducible_with_seed() -> None:
    prompt = "The quick brown fox jumps over the lazy dog"
    first = await BidiSwapConverter(budget=3, seed=5).convert_async(prompt=prompt)
    second = await BidiSwapConverter(budget=3, seed=5).convert_async(prompt=prompt)

    assert first.output_text == second.output_text


async def test_convert_async_different_seeds_change_positions() -> None:
    prompt = "The quick brown fox jumps over the lazy dog"
    outputs = {(await BidiSwapConverter(seed=seed).convert_async(prompt=prompt)).output_text for seed in range(10)}

    assert len(outputs) > 1


async def test_convert_async_every_placement_is_reachable() -> None:
    prompt = "abcde"
    placements = set()
    for seed in range(200):
        result = await BidiSwapConverter(budget=2, seed=seed).convert_async(prompt=prompt)
        placements.add(_strip_controls(result.output_text))

    assert placements == {"badce", "baced", "acbed"}


async def test_convert_async_non_text_input_type_raises() -> None:
    with pytest.raises(ValueError, match="Only 'text' input type is supported"):
        await BidiSwapConverter().convert_async(prompt="Hello", input_type="image_path")


def test_input_supported() -> None:
    converter = BidiSwapConverter()

    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


@pytest.mark.parametrize("budget", [0, -1, True])
def test_init_rejects_invalid_budget(budget: int) -> None:
    with pytest.raises(ValueError, match="budget must be a positive integer"):
        BidiSwapConverter(budget=budget)


def test_identifier_includes_budget_and_seed() -> None:
    params = BidiSwapConverter(budget=2, seed=4).get_identifier().params

    assert params["budget"] == 2
    assert params["seed"] == 4


_NEWLINE = chr(0x0A)
_CARRIAGE_RETURN = chr(0x0D)
_TAB = chr(0x09)
_LINE_SEPARATOR = chr(0x2028)
_PARAGRAPH_SEPARATOR = chr(0x2029)


@pytest.mark.parametrize(
    "separator",
    [_NEWLINE, _CARRIAGE_RETURN + _NEWLINE, _TAB, _LINE_SEPARATOR, _PARAGRAPH_SEPARATOR],
)
async def test_convert_async_never_swaps_a_separator(separator: str) -> None:
    prompt = f"a{separator}"

    result = await BidiSwapConverter(seed=0).convert_async(prompt=prompt)

    assert result.output_text == prompt


async def test_convert_async_swaps_on_both_sides_of_a_newline() -> None:
    result = await BidiSwapConverter(budget=5, seed=0).convert_async(prompt=f"ab{_NEWLINE}cd")

    assert result.output_text == _swap("a", "b") + _NEWLINE + _swap("c", "d")


async def test_convert_async_keeps_crlf_whole_and_unswapped() -> None:
    result = await BidiSwapConverter(budget=5, seed=0).convert_async(prompt=f"ab{_CARRIAGE_RETURN}{_NEWLINE}cd")

    assert result.output_text == _swap("a", "b") + _CARRIAGE_RETURN + _NEWLINE + _swap("c", "d")


async def test_convert_async_separator_limits_how_many_pairs_fit() -> None:
    result = await BidiSwapConverter(budget=5, seed=0).convert_async(prompt=f"abc{_NEWLINE}d")

    assert result.output_text.count(_RLO) == 1
    assert _strip_controls(result.output_text) in {f"bac{_NEWLINE}d", f"acb{_NEWLINE}d"}


async def test_convert_async_reaches_every_placement_around_separators() -> None:
    prompt = f"abc{_NEWLINE}def"
    placements = set()
    for seed in range(200):
        result = await BidiSwapConverter(seed=seed).convert_async(prompt=prompt)
        placements.add(_strip_controls(result.output_text))

    assert placements == {
        f"bac{_NEWLINE}def",
        f"acb{_NEWLINE}def",
        f"abc{_NEWLINE}edf",
        f"abc{_NEWLINE}dfe",
    }


async def test_convert_async_picks_placements_uniformly() -> None:
    prompt = "abcdef"
    counts: dict[str, int] = {}
    for seed in range(600):
        result = await BidiSwapConverter(budget=2, seed=seed).convert_async(prompt=prompt)
        placement = _strip_controls(result.output_text)
        counts[placement] = counts.get(placement, 0) + 1

    # Two non-overlapping pairs fit in six characters in six ways, so each should appear about 100 times.
    assert len(counts) == 6
    assert all(60 <= count <= 140 for count in counts.values())


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("", 0),
        ("a", 0),
        ("abcd", 2),
        ("abcde", 2),
        (f"abc{_NEWLINE}def", 2),
        (f"a{_NEWLINE}b{_NEWLINE}c", 0),
        (f"ab{_TAB}cd", 2),
    ],
)
def test_max_pair_count_counts_pairs_between_separators(prompt: str, expected: int) -> None:
    swappable = [_is_swappable(cluster) for cluster in graphemes(prompt)]

    assert BidiSwapConverter._max_pair_count(swappable=swappable) == expected
