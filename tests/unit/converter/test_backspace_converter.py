# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.converter import BackspaceConverter, ConverterResult

_INSERTION = re.compile(r".\x08", re.DOTALL)


def _remove_insertions(text: str) -> str:
    return _INSERTION.sub("", text)


async def test_convert_async_inserts_one_pair_by_default() -> None:
    result = await BackspaceConverter(seed=0).convert_async(prompt="Hello world")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text.count("\b") == 1
    assert _remove_insertions(result.output_text) == "Hello world"


@pytest.mark.parametrize("budget", [2, 5])
async def test_convert_async_inserts_exactly_budget_pairs(budget: int) -> None:
    result = await BackspaceConverter(budget=budget, seed=0).convert_async(prompt="Hello world")

    assert result.output_text.count("\b") == budget
    assert _remove_insertions(result.output_text) == "Hello world"


async def test_convert_async_budget_larger_than_boundaries_fills_every_boundary() -> None:
    converter = BackspaceConverter(budget=10, characters=["x"], seed=0)

    result = await converter.convert_async(prompt="abc")

    assert result.output_text == "x\bax\bbx\bcx\b"


async def test_convert_async_uses_only_given_characters() -> None:
    converter = BackspaceConverter(budget=12, characters=["#", "~"], seed=0)

    result = await converter.convert_async(prompt="Hello world")

    inserted = {match.group()[0] for match in _INSERTION.finditer(result.output_text)}
    assert inserted <= {"#", "~"}


async def test_convert_async_default_characters_are_printable_ascii() -> None:
    result = await BackspaceConverter(budget=12, seed=0).convert_async(prompt="Hello world")

    for match in _INSERTION.finditer(result.output_text):
        assert match.group()[0] in BackspaceConverter.ASCII_PRINTABLE


def test_ascii_printable_covers_space_to_tilde() -> None:
    assert BackspaceConverter.ASCII_PRINTABLE[0] == " "
    assert BackspaceConverter.ASCII_PRINTABLE[-1] == "~"
    assert len(BackspaceConverter.ASCII_PRINTABLE) == 95


async def test_convert_async_keeps_grapheme_clusters() -> None:
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    converter = BackspaceConverter(budget=3, characters=["x"], seed=0)

    result = await converter.convert_async(prompt=f"{family}e\u0301")

    assert result.output_text == f"x\b{family}x\be\u0301x\b"


async def test_convert_async_empty_prompt_stays_empty() -> None:
    result = await BackspaceConverter(seed=0).convert_async(prompt="")

    assert result.output_text == ""


async def test_convert_async_is_reproducible_with_seed() -> None:
    prompt = "The quick brown fox jumps over the lazy dog"
    first = await BackspaceConverter(budget=3, seed=9).convert_async(prompt=prompt)
    second = await BackspaceConverter(budget=3, seed=9).convert_async(prompt=prompt)

    assert first.output_text == second.output_text


async def test_convert_async_different_seeds_change_output() -> None:
    prompt = "The quick brown fox jumps over the lazy dog"
    outputs = {(await BackspaceConverter(seed=seed).convert_async(prompt=prompt)).output_text for seed in range(10)}

    assert len(outputs) > 1


async def test_convert_async_non_text_input_type_raises() -> None:
    with pytest.raises(ValueError, match="Only 'text' input type is supported"):
        await BackspaceConverter().convert_async(prompt="Hello", input_type="image_path")


def test_input_supported() -> None:
    converter = BackspaceConverter()

    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


@pytest.mark.parametrize("budget", [0, -1, True])
def test_init_rejects_invalid_budget(budget: int) -> None:
    with pytest.raises(ValueError, match="budget must be a positive integer"):
        BackspaceConverter(budget=budget)


@pytest.mark.parametrize("characters", [[], [""], ["ab"], ["\b"], ["a", "\b"]])
def test_init_rejects_invalid_characters(characters: list[str]) -> None:
    with pytest.raises(ValueError, match="characters must be a non-empty sequence"):
        BackspaceConverter(characters=characters)


def test_default_identifier_omits_characters() -> None:
    params = BackspaceConverter().get_identifier().params

    assert params["budget"] == 1
    assert "characters" not in params


def test_identifier_includes_budget_characters_and_seed() -> None:
    params = BackspaceConverter(budget=2, characters=["x", "y"], seed=3).get_identifier().params

    assert params["budget"] == 2
    assert params["characters"] == ["x", "y"]
    assert params["seed"] == 3
