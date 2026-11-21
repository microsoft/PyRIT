# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, FlipConverter


async def test_flip_converter_basic():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="hello", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "olleh"
    assert result.output_type == "text"


async def test_flip_converter_sentence():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="hello world", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "dlrow olleh"
    assert result.output_type == "text"


async def test_flip_converter_empty():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == ""
    assert result.output_type == "text"


async def test_flip_converter_single_char():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="a", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "a"
    assert result.output_type == "text"


async def test_flip_converter_palindrome():
    converter = FlipConverter()
    result = await converter.convert_async(prompt="racecar", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "racecar"
    assert result.output_type == "text"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("🇮🇳 flag", "galf 🇮🇳"),
        ("👨‍👩‍👧 family", "ylimaf 👨‍👩‍👧"),
        ("👍🏽 OK", "KO 👍🏽"),
        ("1️⃣ test", "tset 1️⃣"),
        ("cafe\u0301", "e\u0301fac"),
        ("\u0928\u092e\u0938\u094d\u0924\u0947", "\u0938\u094d\u0924\u0947\u092e\u0928"),
        ("क्षमा", "माक्ष"),
        ("hello world", "dlrow olleh"),
    ],
)
async def test_flip_converter_reverses_grapheme_clusters_async(*, prompt: str, expected: str) -> None:
    result = await FlipConverter().convert_async(prompt=prompt)

    assert result.output_text == expected


@pytest.mark.parametrize("prompt", ["hello", "क्षमा", "नमस्ते", "cafe\u0301", "👨‍👩‍👧 family"])
async def test_flip_converter_self_inverse_async(prompt: str) -> None:
    converter = FlipConverter()
    flipped = await converter.convert_async(prompt=prompt)
    double_flipped = await converter.convert_async(prompt=flipped.output_text)

    assert double_flipped.output_text == prompt


async def test_flip_converter_resegmentation_edge_cases_async() -> None:
    converter = FlipConverter()

    # Unpaired regional indicator symbols re-pair when reversed
    unpaired_flags = "🇮🇳🇺🇸🇫"
    flipped_flags = (await converter.convert_async(prompt=unpaired_flags)).output_text
    double_flipped_flags = (await converter.convert_async(prompt=flipped_flags)).output_text
    assert flipped_flags == "🇫🇺🇸🇮🇳"
    assert double_flipped_flags != unpaired_flags

    # Leading combining marks attach to preceding characters when reversed
    combining = "\u0301abc"
    flipped_combining = (await converter.convert_async(prompt=combining)).output_text
    double_flipped_combining = (await converter.convert_async(prompt=flipped_combining)).output_text
    assert flipped_combining == "cba\u0301"
    assert double_flipped_combining != combining


async def test_flip_converter_input_not_supported():
    converter = FlipConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image_path")
