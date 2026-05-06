# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.prompt_converter import ConverterResult
from pyrit.prompt_converter.unicode_confusable_converter import (
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
    "pyrit.prompt_converter.unicode_confusable_converter.is_confusable",
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
