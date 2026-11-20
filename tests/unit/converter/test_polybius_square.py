# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter._polybius_square import (
    FULL_ALPHABET,
    IJ_MERGED_ALPHABET,
    PolybiusSquare,
    build_keyed_alphabet,
    sanitize_to_alphabet,
)


def test_build_keyed_alphabet_plain_is_reduced_alphabet():
    assert "".join(build_keyed_alphabet("")) == IJ_MERGED_ALPHABET


def test_build_keyed_alphabet_dedupes_and_appends_remaining():
    # "PLAYFAIR" with J folded out: P L A Y F I R, then the rest of the alphabet in order.
    result = "".join(build_keyed_alphabet("PLAYFAIR", merge=("J", "I")))
    assert result.startswith("PLAYFIR")
    assert len(result) == 25
    assert sorted(result) == sorted(IJ_MERGED_ALPHABET)


def test_build_keyed_alphabet_folds_merge_letter_in_key():
    # A key of only J should fold to I, so the ordering begins with I.
    assert "".join(build_keyed_alphabet("JJJ", merge=("J", "I")))[0] == "I"


def test_build_keyed_alphabet_ignores_non_alphabet_chars():
    assert "".join(build_keyed_alphabet("a1 b!c", merge=("J", "I"))).startswith("ABC")


def test_build_keyed_alphabet_custom_alphabet_minus_q():
    # Omit Q instead of merging I/J: 25-letter alphabet keeps I and J, drops Q.
    alphabet = "".join(c for c in FULL_ALPHABET if c != "Q")
    result = "".join(build_keyed_alphabet("EXAMPLE", alphabet=alphabet))
    assert "Q" not in result
    assert "J" in result and "I" in result
    assert result.startswith("EXAMPL")


def test_build_keyed_alphabet_append_adds_filler_symbol():
    # Trifid-style 27-symbol cube alphabet: 26 letters plus a filler.
    result = build_keyed_alphabet("", alphabet=FULL_ALPHABET, append="+")
    assert len(result) == 27
    assert result[-1] == "+"


def test_sanitize_to_alphabet_folds_and_drops_non_letters():
    alphabet_set = set(IJ_MERGED_ALPHABET)
    assert sanitize_to_alphabet("He llo, J!", alphabet_set=alphabet_set, merge=("J", "I")) == list("HELLOI")


def test_polybius_square_lookups_roundtrip():
    square = PolybiusSquare(build_keyed_alphabet(""))
    for letter, (row, col) in square.position.items():
        assert square.grid[row][col] == letter


def test_polybius_square_rejects_wrong_length():
    with pytest.raises(ValueError):
        PolybiusSquare(list("ABC"))
