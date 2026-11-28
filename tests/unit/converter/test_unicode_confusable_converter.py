# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.converter import (
    ConverterResult,
    UnicodeConfusableConverter,
)


@pytest.fixture
def homoglyphs_converter():
    return UnicodeConfusableConverter(source_package="confusable_homoglyphs")


@pytest.fixture
def confusables_converter():
    return UnicodeConfusableConverter(source_package="confusables")


def test_input_supported(homoglyphs_converter):
    assert homoglyphs_converter.input_supported("text") is True
    assert homoglyphs_converter.input_supported("image") is False


def test_get_homoglyph_variants(homoglyphs_converter):
    word = "test"
    variants = homoglyphs_converter._get_homoglyph_variants(word)
    assert isinstance(variants, list)
    assert word not in variants  # Original word should not be in variants
    # Since homoglyph variants depend on the external library and mappings,
    # we cannot assert exact variants, but we can check that variants are different
    for variant in variants:
        assert variant != word


def test_get_homoglyph_variants_no_variants(homoglyphs_converter):
    word = "xxxx"  # A word with no expected homoglyphs
    variants = homoglyphs_converter._get_homoglyph_variants(word)
    assert len(variants) == 0 or "᙮" in variants


def test_generate_perturbed_prompts(homoglyphs_converter):
    prompt = "This is a test."
    perturbed_prompt = homoglyphs_converter._generate_perturbed_prompts(prompt)
    assert isinstance(perturbed_prompt, str)
    # Ensure that the perturbed prompt is different from the original
    assert perturbed_prompt != prompt


async def test_homoglyphs_converter_convert_async(homoglyphs_converter):
    prompt = "This is a test."
    result = await homoglyphs_converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    perturbed_prompts = result.output_text.split("\n")
    for p_prompt in perturbed_prompts:
        assert p_prompt != prompt
        assert isinstance(p_prompt, str)


async def test_confusables_converter_convert_async(confusables_converter) -> None:
    confusables_converter._deterministic = True
    output = await confusables_converter.convert_async(prompt="lorem ipsum dolor sit amet", input_type="text")
    assert output.output_text == "ïỎ𐒴ḕ𝗠 ïṗṡ𝘶𝗠 𝑫ỎïỎ𐒴 ṡï𝚝 ḁ𝗠ḕ𝚝"
    assert output.output_type == "text"


async def test_cyrillic_homoglyph_converter_convert_async() -> None:
    converter = UnicodeConfusableConverter(source_package="cyrillic_homoglyph")
    output = await converter.convert_async(prompt="aceopxy ACEOPXY Copy example 3.14!", input_type="text")
    assert output.output_text == (
        "\u0430\u0441\u0435\u043e\u0440\u0445\u0443 \u0410\u0421\u0415\u041e\u0420\u0425\u0423 "
        "\u0421\u043e\u0440\u0443 \u0435\u0445\u0430m\u0440l\u0435 3.14!"
    )
    assert output.output_type == "text"


def test_invalid_source_package_raises() -> None:
    with pytest.raises(ValueError, match="Invalid source package"):
        UnicodeConfusableConverter(source_package="unknown")


async def test_input_not_supported(homoglyphs_converter):
    with pytest.raises(ValueError):
        await homoglyphs_converter.convert_async(prompt="This is a test.", input_type="image")


async def test_convert_async_non_ascii_word(homoglyphs_converter, confusables_converter):
    prompt = "café"
    result = await homoglyphs_converter.convert_async(prompt=prompt, input_type="text")
    result_confusables = await confusables_converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert isinstance(result_confusables, ConverterResult)
    perturbed_prompt = result.output_text
    perturbed_prompt_confusables = result_confusables.output_text

    # Assert that we are getting perturbed results of the correct type and that it's not the same as original
    assert isinstance(perturbed_prompt, str)
    assert isinstance(perturbed_prompt_confusables, str)
    assert perturbed_prompt != prompt, "The perturbed prompt should be different from the original prompt."
    assert perturbed_prompt_confusables != prompt

    # Check if non-ASCII character 'é' is handled correctly
    assert "é" in perturbed_prompt or "e" in perturbed_prompt, "The non-ASCII character 'é' should be handled properly."


@patch(
    "pyrit.converter.unicode_confusable_converter.is_confusable",
    side_effect=UnicodeDecodeError("codec", b"", 0, 1, "reason"),
)
def test_get_homoglyph_variants_unicode_error(mock_is_confusable, homoglyphs_converter):
    # Test to check homoglyphs when UnicodeDecodeError is raised
    variants = homoglyphs_converter._get_homoglyph_variants("test")
    assert variants == []  # Ensure the result is an empty list when the exception is raised


async def test_convert_async_empty_prompt(homoglyphs_converter, confusables_converter):
    prompt = ""
    result = await homoglyphs_converter.convert_async(prompt=prompt, input_type="text")
    result_confusables = await confusables_converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert isinstance(result_confusables, ConverterResult)
    assert result.output_text == ""
    assert result_confusables.output_text == ""


async def test_confusables_converter_space_input(confusables_converter) -> None:
    output = await confusables_converter.convert_async(prompt="  ", input_type="text")
    assert output.output_text == "  "
    assert output.output_type == "text"


async def test_convert_async_long_prompt(homoglyphs_converter, confusables_converter):
    prompt = (
        "This is a long prompt intended to test how the ConfusableConverter handles larger inputs. "
        "It contains multiple sentences, various punctuation marks, and a mix of short and long words."
    )
    result = await homoglyphs_converter.convert_async(prompt=prompt, input_type="text")
    result_confusables = await confusables_converter.convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert isinstance(result_confusables, ConverterResult)
    perturbed_prompts = result.output_text.split("\n")
    perturbed_prompts_confusables = result_confusables.output_text.split("\n")
    for p_prompt in perturbed_prompts:
        assert p_prompt != prompt
        assert isinstance(p_prompt, str)
    for p_prompt in perturbed_prompts_confusables:
        assert p_prompt != prompt
        assert isinstance(p_prompt, str)


def _changed_positions(original: str, converted: str) -> list[int]:
    assert len(original) == len(converted)
    return [index for index, (before, after) in enumerate(zip(original, converted, strict=True)) if before != after]


@pytest.mark.parametrize("source_package", ["confusable_homoglyphs", "confusables", "cyrillic_homoglyph"])
@pytest.mark.parametrize("budget", [1, 3])
async def test_budget_replaces_exactly_budget_characters(source_package: str, budget: int) -> None:
    # Every letter here has single-character look-alikes in each package, so lengths stay comparable.
    prompt = "the best yes"
    converter = UnicodeConfusableConverter(source_package=source_package, budget=budget)

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert len(_changed_positions(prompt, result.output_text)) == budget


async def test_budget_larger_than_candidates_replaces_every_candidate() -> None:
    converter = UnicodeConfusableConverter(source_package="cyrillic_homoglyph", budget=50)

    result = await converter.convert_async(prompt="copy 3.14", input_type="text")

    assert result.output_text == "\u0441\u043e\u0440\u0443 3.14"


async def test_budget_without_candidates_returns_prompt_unchanged() -> None:
    converter = UnicodeConfusableConverter(source_package="cyrillic_homoglyph", budget=1)

    result = await converter.convert_async(prompt="123 !?", input_type="text")

    assert result.output_text == "123 !?"


async def test_budget_cyrillic_uses_table_substitutes() -> None:
    prompt = "aceopxy"
    converter = UnicodeConfusableConverter(source_package="cyrillic_homoglyph", budget=2)

    result = await converter.convert_async(prompt=prompt, input_type="text")

    for index in _changed_positions(prompt, result.output_text):
        assert result.output_text[index] == prompt[index].translate(UnicodeConfusableConverter._CYRILLIC_HOMOGLYPHS)


async def test_budget_confusables_skips_spaces() -> None:
    converter = UnicodeConfusableConverter(source_package="confusables", budget=50)

    result = await converter.convert_async(prompt="a b", input_type="text")

    assert result.output_text[1] == " "
    assert _changed_positions("a b", result.output_text) == [0, 2]


async def test_budget_deterministic_picks_last_substitute() -> None:
    converter = UnicodeConfusableConverter(source_package="confusables", budget=50, deterministic=True)

    result = await converter.convert_async(prompt="a", input_type="text")

    assert result.output_text == converter._substitutes("a")[-1]


async def test_budget_empty_prompt_stays_empty() -> None:
    converter = UnicodeConfusableConverter(budget=1)

    result = await converter.convert_async(prompt="", input_type="text")

    assert result.output_text == ""


def test_substitutes_exclude_original_character() -> None:
    for source_package in ("confusable_homoglyphs", "confusables", "cyrillic_homoglyph"):
        converter = UnicodeConfusableConverter(source_package=source_package)
        assert "a" not in converter._substitutes("a")


@pytest.mark.parametrize("budget", [0, -2, True])
def test_invalid_budget_raises(budget: int) -> None:
    with pytest.raises(ValueError, match="budget must be a positive integer"):
        UnicodeConfusableConverter(budget=budget)


def test_default_identifier_has_no_budget() -> None:
    assert "budget" not in UnicodeConfusableConverter().get_identifier().params


def test_identifier_includes_budget() -> None:
    assert UnicodeConfusableConverter(budget=2).get_identifier().params["budget"] == 2
