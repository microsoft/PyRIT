# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random

import pytest

from pyrit.converter.puzzled.puzzle_builders import (
    PuzzleType,
    build_anagram,
    build_crossword,
    build_word_search,
    crossword_symbol_map,
)

# The eight directions a word may run in the grid, mirroring the builder.
_DIRECTIONS = [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def _parse_grid(grid_str: str) -> list[list[str]]:
    return [row.split(" ") for row in grid_str.split("\n")]


def _find_word(grid: list[list[str]], word: str) -> bool:
    size = len(grid)
    for row in range(size):
        for col in range(size):
            for row_delta, col_delta in _DIRECTIONS:
                end_row = row + row_delta * (len(word) - 1)
                end_col = col + col_delta * (len(word) - 1)
                if (
                    0 <= end_row < size
                    and 0 <= end_col < size
                    and all(grid[row + row_delta * i][col + col_delta * i] == word[i] for i in range(len(word)))
                ):
                    return True
    return False


def test_puzzle_type_values():
    assert PuzzleType.WORD_SEARCH.value == "word_search"
    assert PuzzleType.ANAGRAM.value == "anagram"
    assert PuzzleType.CROSSWORD.value == "crossword"


# --- anagram ---------------------------------------------------------------


def test_anagram_is_permutation_of_concatenated_letters():
    words = ["abduct", "vault"]
    result = build_anagram(words, random.Random(1))
    assert sorted(result) == sorted("ABDUCTVAULT")


def test_anagram_is_reproducible_with_same_seed():
    words = ["abduct", "vault", "system"]
    assert build_anagram(words, random.Random(42)) == build_anagram(words, random.Random(42))


def test_anagram_empty_list_raises():
    with pytest.raises(ValueError):
        build_anagram([], random.Random(0))


def test_anagram_whitespace_only_word_raises():
    with pytest.raises(ValueError):
        build_anagram(["abduct", "   "], random.Random(0))


# --- crossword -------------------------------------------------------------


def test_crossword_symbol_map_picks_top_three_shared_letters():
    mapping = crossword_symbol_map(["ABDUCT", "COMPUTER", "DESTROY"])
    # T appears in all three (freq 3); C, D tie at freq 2 and win alphabetically.
    assert mapping == {"T": "#", "C": "*", "D": "@"}


def test_crossword_symbol_map_ignores_unshared_letters():
    # No letter is shared across two of these words, so nothing is masked.
    assert crossword_symbol_map(["cat", "dog"]) == {}


def test_crossword_build_applies_mapping_per_word():
    result = build_crossword(["ABDUCT", "COMPUTER", "DESTROY"])
    assert result == "1. AB@U*#\n2. *OMPU#ER\n3. @ES#ROY"


def test_crossword_single_word_has_no_symbols():
    assert build_crossword(["abduct"]) == "1. ABDUCT"


def test_crossword_empty_raises():
    with pytest.raises(ValueError):
        build_crossword([])


# --- word search -----------------------------------------------------------


def test_word_search_is_reproducible_with_same_seed():
    words = ["abduct", "vault", "system"]
    assert build_word_search(words, random.Random(7)) == build_word_search(words, random.Random(7))


def test_word_search_contains_every_word():
    words = ["ABDUCT", "VAULT", "SYSTEM"]
    grid = _parse_grid(build_word_search(words, random.Random(123)))
    for word in words:
        assert _find_word(grid, word), f"{word} not found in grid"


def test_word_search_is_square_and_fits_longest_word():
    words = ["extraordinarily", "cat"]
    grid = _parse_grid(build_word_search(words, random.Random(3)))
    size = len(grid)
    assert size >= len("EXTRAORDINARILY")
    assert all(len(row) == size for row in grid)


def test_word_search_fills_gaps_with_uppercase_letters():
    grid_str = build_word_search(["abduct"], random.Random(9))
    letters = grid_str.replace("\n", "").replace(" ", "")
    assert letters.isalpha() and letters.isupper()


def test_word_search_empty_raises():
    with pytest.raises(ValueError):
        build_word_search([], random.Random(0))
