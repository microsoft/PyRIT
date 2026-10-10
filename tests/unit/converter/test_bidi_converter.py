# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import BidiConverter, ConverterResult

# Bidirectional control characters, built from code points to keep this file pure ASCII
RLO = chr(0x202E)  # Right-to-left override
RLE = chr(0x202B)  # Right-to-left embedding
PDF = chr(0x202C)  # Pop directional formatting
RLI = chr(0x2067)  # Right-to-left isolate
PDI = chr(0x2069)  # Pop directional isolate


def test_input_supported():
    converter = BidiConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image") is False


async def test_default_scheme_wraps_in_rlo_override():
    result = await BidiConverter().convert_async(prompt="abc", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert result.output_text == f"{RLO}abc{PDF}"


async def test_embedding_scheme():
    result = await BidiConverter(scheme="embedding").convert_async(prompt="abc")
    assert result.output_text == f"{RLE}abc{PDF}"


async def test_isolate_scheme():
    result = await BidiConverter(scheme="isolate").convert_async(prompt="abc")
    assert result.output_text == f"{RLI}abc{PDI}"


async def test_empty_prompt_returns_empty():
    result = await BidiConverter().convert_async(prompt="")
    assert result.output_text == ""


@pytest.mark.parametrize("scheme", ["override", "embedding", "isolate"])
async def test_conversion_is_deterministic(scheme):
    converter = BidiConverter(scheme=scheme)
    first = await converter.convert_async(prompt="some prompt")
    second = await converter.convert_async(prompt="some prompt")
    assert first.output_text == second.output_text


def test_invalid_scheme_raises():
    with pytest.raises(ValueError):
        BidiConverter(scheme="nonsense")


async def test_input_type_not_supported_raises():
    with pytest.raises(ValueError):
        await BidiConverter().convert_async(prompt="abc", input_type="image")
