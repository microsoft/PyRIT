# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import ConverterResult, UpsideDownConverter


async def _convert(prompt: str) -> str:
    result = await UpsideDownConverter().convert_async(prompt=prompt, input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    return result.output_text


async def test_simple_word_is_mapped_and_reversed():
    # "hello" -> map each: h->ɥ e->ǝ l->ꞁ l->ꞁ o->o, then reverse -> "oꞁꞁǝɥ"
    assert await _convert("hello") == "oꞁꞁǝɥ"


async def test_output_reads_as_reversed_sequence():
    # The whole string is reversed, so the first source char ends up last.
    result = await _convert("ab")
    # a->ɐ, b->q ; reversed order -> q then ɐ
    assert result == "qɐ"


async def test_empty_string_returns_empty():
    assert await _convert("") == ""


async def test_space_is_preserved_but_repositioned():
    # "a b" -> a->ɐ, space, b->q ; reversed -> "q ɐ"
    assert await _convert("a b") == "q ɐ"


async def test_unmapped_characters_pass_through_unchanged():
    # '@', '#', '%', emoji, and non-ASCII letters have no mapping; only their order flips.
    assert await _convert("a@#") == "#@ɐ"
    # "café" reversed is é,f,a,c -> é (unmapped), f->ɟ, a->ɐ, c->ɔ
    assert await _convert("café") == "éɟɐɔ"


async def test_digits_six_and_nine_swap():
    assert await _convert("69") == "69"  # 6->9, 9->6, reversed -> "69"
    assert await _convert("96") == "96"


async def test_symmetric_characters_map_to_themselves():
    # o, s, x, z, H, I, N, O, S, X, Z, 0, 1, 8 are left unchanged (only reordered).
    for ch in ["o", "s", "x", "z", "H", "I", "N", "O", "S", "X", "Z", "0", "1", "8"]:
        assert await _convert(ch) == ch


async def test_brackets_swap_so_pair_reads_upright_after_reversal():
    # "(a)" -> '(' -> ')', a -> ɐ, ')' -> '(' ; reversed -> "(ɐ)"
    assert await _convert("(a)") == "(ɐ)"
    assert await _convert("[x]") == "[x]"
    assert await _convert("{1}") == "{1}"


async def test_punctuation_inversions():
    assert await _convert("!") == "¡"  # inverted exclamation
    assert await _convert("?") == "¿"  # inverted question
    assert await _convert(".") == "˙"  # dot above


async def test_sentence_is_mapped_and_reversed():
    # W->M, h->ɥ, y->ʎ, ?->¿ ; reversed -> ¿ ʎ ɥ M
    assert await _convert("Why?") == "¿ʎɥM"


async def test_all_ascii_lowercase_letters_change_except_symmetric():
    # Every lowercase letter except the rotation-symmetric o, s, x, z must be transformed.
    symmetric = {"o", "s", "x", "z"}
    for letter in "abcdefghijklmnopqrstuvwxyz":
        out = await _convert(letter)
        if letter in symmetric:
            assert out == letter
        else:
            assert out != letter, f"{letter!r} should have an upside-down form"


@pytest.mark.parametrize("bad_type", ["image_path", "audio_path"])
async def test_unsupported_input_type_raises(bad_type):
    with pytest.raises(ValueError):
        await UpsideDownConverter().convert_async(prompt="hello", input_type=bad_type)
