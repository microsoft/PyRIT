# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_converter import ZalgoConverter


@pytest.mark.asyncio
async def test_zalgo_output_changes_text():
    prompt = "hello"
    converter = ZalgoConverter(intensity=5, seed=42)
    result = await converter.convert_async(prompt=prompt)
    assert result.output_text != prompt
    assert all(c in result.output_text for c in prompt)  # should still contain all original letters


@pytest.mark.asyncio
async def test_zalgo_reproducible_seed():
    prompt = "seed test"
    converter1 = ZalgoConverter(intensity=5, seed=123)
    converter2 = ZalgoConverter(intensity=5, seed=123)
    result1 = await converter1.convert_async(prompt=prompt)
    result2 = await converter2.convert_async(prompt=prompt)
    assert result1.output_text == result2.output_text


@pytest.mark.asyncio
async def test_zalgo_zero_intensity_returns_original():
    prompt = "no chaos please"
    converter = ZalgoConverter(intensity=0)
    result = await converter.convert_async(prompt=prompt)
    assert result.output_text == prompt


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_zalgo_float_intensity():
    prompt = "test string"
    converter = ZalgoConverter(intensity=5.5, seed=1)
    result = await converter.convert_async(prompt=prompt)
    assert isinstance(result.output_text, str)
    assert len(result.output_text) > len(prompt)


@pytest.mark.asyncio
async def test_zalgo_string_intensity():
    prompt = "test string"
    converter = ZalgoConverter(intensity="7", seed=1)
    result = await converter.convert_async(prompt=prompt)
    assert isinstance(result.output_text, str)
    assert len(result.output_text) > len(prompt)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_intensity", ["this isn't an int", None])
async def test_zalgo_invalid_intensity(bad_intensity):
    with pytest.raises(ValueError):
        ZalgoConverter(intensity=bad_intensity, seed=1)
