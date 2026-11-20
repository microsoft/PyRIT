# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, MirrorConverter


async def _convert(prompt: str) -> str:
    result = await MirrorConverter().convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    return result.output_text


async def test_mirror_maps_and_reverses():
    # "be" -> map b->d, e->ɘ -> "dɘ" -> reversed -> "ɘd"
    assert await _convert("be") == "ɘd"


async def test_mirror_lowercase_sequence():
    # "abc" -> "ɒdɔ" -> reversed -> "ɔdɒ"
    assert await _convert("abc") == "ɔdɒ"


async def test_mirror_brackets_are_swapped_then_reversed():
    # Brackets flip to their partner so that after the whole string is reversed they read correctly.
    # "(hi)" -> ")hi(" -> reversed -> "(ih)"
    assert await _convert("(hi)") == "(ih)"


async def test_mirror_unmapped_characters_pass_through():
    # Symmetric characters have no entry and are only reversed, not substituted.
    assert await _convert("XOXO") == "OXOX"


async def test_mirror_empty_string():
    assert await _convert("") == ""


async def test_mirror_question_mark():
    assert await _convert("?") == "⸮"


async def test_mirror_leaves_spaces_and_unmapped_punctuation():
    # Space is symmetric/unmapped; it is kept, letters are mirrored, and order is reversed.
    # "a b" -> "ɒ d" -> reversed -> "d ɒ"
    assert await _convert("a b") == "d ɒ"


async def test_mirror_bd_and_pq_are_mutual_inverses():
    mapping = MirrorConverter._MIRROR_MAP
    assert mapping["b"] == "d" and mapping["d"] == "b"
    assert mapping["p"] == "q" and mapping["q"] == "p"
    for left, right in [("(", ")"), ("[", "]"), ("{", "}"), ("<", ">"), ("/", "\\")]:
        assert mapping[left] == right and mapping[right] == left


async def test_mirror_mapped_values_are_single_characters():
    # Each mirrored glyph is a single code point, keeping length stable per character.
    for source, mirrored in MirrorConverter._MIRROR_MAP.items():
        assert len(source) == 1
        assert len(mirrored) == 1


async def test_mirror_rejects_unsupported_input_type():
    with pytest.raises(ValueError):
        await MirrorConverter().convert_async(prompt="hello", input_type="image_path")
