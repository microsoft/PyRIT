# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random

import pytest

from pyrit.converter import ZalgoConverter
from pyrit.converter.text_selection_strategy import WordProportionSelectionStrategy


async def test_zalgo_output_changes_text():
    prompt = "hello"
    converter = ZalgoConverter(intensity=5, seed=42)
    result = await converter.convert_async(prompt=prompt)
    assert result.output_text != prompt
    assert all(c in result.output_text for c in prompt)  # should still contain all original letters


async def test_zalgo_reproducible_seed():
    prompt = "seed test"
    converter1 = ZalgoConverter(intensity=5, seed=123)
    converter2 = ZalgoConverter(intensity=5, seed=123)
    result1 = await converter1.convert_async(prompt=prompt)
    result2 = await converter2.convert_async(prompt=prompt)
    assert result1.output_text == result2.output_text


async def test_zalgo_seed_does_not_disturb_global_rng():
    """A seeded converter must not reseed the process-wide RNG."""
    original_state = random.getstate()
    try:
        # Seed to a value distinct from the converter's own seed. Without this the
        # assertion can pass vacuously: a leaking component that reseeds to the same
        # value the previous test used lands back on the captured state.
        random.seed(0)
        state_before = random.getstate()

        converter = ZalgoConverter(intensity=5, seed=42)
        await converter.convert_async(prompt="seed test")

        assert random.getstate() == state_before
    finally:
        # Leave process-wide RNG exactly as found so test order stays irrelevant.
        random.setstate(original_state)


async def test_zalgo_seed_is_repeatable_on_same_instance():
    prompt = "seed test"
    converter = ZalgoConverter(intensity=5, seed=123)
    first = await converter.convert_async(prompt=prompt)
    second = await converter.convert_async(prompt=prompt)
    assert first.output_text == second.output_text


async def test_zalgo_unseeded_converters_stay_independent():
    """An unseeded converter must keep producing varied output."""
    converter = ZalgoConverter(intensity=5)
    outputs = {(await converter.convert_async(prompt="seed test")).output_text for _ in range(5)}
    assert len(outputs) > 1


async def test_zalgo_seed_is_inherited_by_unseeded_random_selection():
    """
    An outer converter seed is the root for nested components unless they override it.
    """
    prompt = "alpha bravo charlie delta echo foxtrot golf hotel"

    inherited_selection = ZalgoConverter(
        intensity=3,
        seed=42,
        word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5),
    )
    repeated = {(await inherited_selection.convert_async(prompt=prompt)).output_text for _ in range(5)}
    assert len(repeated) == 1

    explicit_selection = ZalgoConverter(
        intensity=3,
        seed=42,
        word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5, seed=7),
    )
    explicitly_repeated = {(await explicit_selection.convert_async(prompt=prompt)).output_text for _ in range(5)}
    assert len(explicitly_repeated) == 1


async def test_zalgo_zero_intensity_returns_original():
    prompt = "no chaos please"
    converter = ZalgoConverter(intensity=0)
    result = await converter.convert_async(prompt=prompt)
    assert result.output_text == prompt


async def test_zalgo_intensity_caps_at_max(caplog):
    prompt = "much zalgo!"
    converter = ZalgoConverter(intensity=1000, seed=1)
    result = await converter.convert_async(prompt=prompt)
    # Should still complete successfully without crashing and adjust to max intensity
    # check if it warns
    assert any(
        record.levelname == "WARNING" and "ZalgoConverter supports intensity" in record.message
        for record in caplog.records
    )
    assert isinstance(result.output_text, str)
    assert len(result.output_text) > len(prompt)


async def test_zalgo_float_intensity():
    prompt = "test string"
    converter = ZalgoConverter(intensity=5.5, seed=1)
    result = await converter.convert_async(prompt=prompt)
    assert isinstance(result.output_text, str)
    assert len(result.output_text) > len(prompt)


async def test_zalgo_string_intensity():
    prompt = "test string"
    converter = ZalgoConverter(intensity="7", seed=1)
    result = await converter.convert_async(prompt=prompt)
    assert isinstance(result.output_text, str)
    assert len(result.output_text) > len(prompt)


async def test_zalgo_negative_intensity(caplog):
    prompt = "test string"
    converter = ZalgoConverter(intensity=-300, seed=1)
    result = await converter.convert_async(prompt=prompt)
    assert isinstance(result.output_text, str)
    assert len(result.output_text) == len(prompt)
    assert any(
        record.levelname == "WARNING" and "ZalgoConverter supports intensity" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize("bad_intensity", ["this isn't an int", None])
async def test_zalgo_invalid_intensity(bad_intensity):
    with pytest.raises(ValueError):
        ZalgoConverter(intensity=bad_intensity, seed=1)
