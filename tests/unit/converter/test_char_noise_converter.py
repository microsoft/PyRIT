# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import CharNoiseConverter, ConverterResult


async def test_char_noise_zero_probability_is_identity():
    converter = CharNoiseConverter(noise_probability=0.0)
    result = await converter.convert_async(prompt="Hello, world!", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "Hello, world!"
    assert result.output_type == "text"


async def test_char_noise_preserves_length():
    converter = CharNoiseConverter(noise_probability=1.0)
    prompt = "the quick brown fox"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert len(result.output_text) == len(prompt)


async def test_char_noise_full_probability_shifts_letters():
    # Every ASCII letter has an in-range neighbor, so at probability 1.0 the output
    # differs from the input and every character stays printable ASCII.
    converter = CharNoiseConverter(noise_probability=1.0)
    prompt = "abcdefghijklmnop"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text != prompt
    assert len(result.output_text) == len(prompt)
    assert all(" " <= c <= "~" for c in result.output_text)


async def test_char_noise_full_probability_shifts_ascii_boundaries():
    converter = CharNoiseConverter(noise_probability=1.0)
    result = await converter.convert_async(prompt=" ~", input_type="text")
    assert result.output_text == "!}"


async def test_char_noise_leaves_non_ascii_untouched():
    converter = CharNoiseConverter(noise_probability=1.0)
    result = await converter.convert_async(prompt="cafe naive: éï", input_type="text")
    # Non-ASCII characters are never perturbed.
    assert "é" in result.output_text
    assert "ï" in result.output_text


async def test_char_noise_resamples_each_call():
    # Fresh randomness per call: two passes over a long prompt differ.
    converter = CharNoiseConverter(noise_probability=0.5)
    prompt = "the quick brown fox jumps over the lazy dog " * 3
    first = (await converter.convert_async(prompt=prompt, input_type="text")).output_text
    second = (await converter.convert_async(prompt=prompt, input_type="text")).output_text
    assert first != second


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_char_noise_rejects_out_of_range_probability(bad):
    with pytest.raises(ValueError, match="noise_probability must be between 0.0 and 1.0"):
        CharNoiseConverter(noise_probability=bad)


async def test_char_noise_rejects_unsupported_input_type():
    converter = CharNoiseConverter()
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="hello", input_type="image_path")
