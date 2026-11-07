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


class _NeverFits(random.Random):
    """An rng that always starts a left-to-right word in the last column, so it never fits."""

    def choice(self, seq):
        return (0, 1)

    def randrange(self, *args, **kwargs):
        return args[0] - 1


class _NeverShuffles(random.Random):
    """An rng that leaves the anagram source unchanged."""

    def shuffle(self, x):
        return None


def test_puzzle_type_values():
    assert PuzzleType.WORD_SEARCH.value == "word_search"
    assert PuzzleType.ANAGRAM.value == "anagram"
    assert PuzzleType.CROSSWORD.value == "crossword"


@pytest.mark.parametrize(
    "puzzle_type, label",
    [(PuzzleType.WORD_SEARCH, "word search"), (PuzzleType.ANAGRAM, "anagram"), (PuzzleType.CROSSWORD, "crossword")],
)
def test_puzzle_type_label_is_prose(puzzle_type, label):
    assert puzzle_type.label == label


# --- anagram ---------------------------------------------------------------


def test_anagram_is_permutation_of_concatenated_letters():
    words = ["abduct", "vault"]
    result = build_anagram(words, random.Random(1))
    assert sorted(result) == sorted("ABDUCTVAULT")


def test_anagram_is_reproducible_with_same_seed():
    words = ["abduct", "vault", "system"]
    assert build_anagram(words, random.Random(42)) == build_anagram(words, random.Random(42))


def test_anagram_retries_when_first_shuffle_is_unchanged():
    assert build_anagram(["AB"], random.Random(0)) == "BA"


def test_anagram_returns_only_possible_arrangement():
    assert build_anagram(["AA"], random.Random(0)) == "AA"


def test_anagram_uses_fallback_when_shuffles_never_change_source():
    result = build_anagram(["ABC"], _NeverShuffles())

    assert result == "BCA"


@pytest.mark.parametrize("mark", ["\u0301", pytest.param("\u05c8", id="unicode18")])
def test_anagram_fallback_keeps_marks_with_their_letters(mark: str) -> None:
    assert build_anagram([f"q{mark}b"], _NeverShuffles()) == f"BQ{mark}"


@pytest.mark.parametrize("mark", ["\u0301", pytest.param("\u05c8", id="unicode18")])
def test_anagram_single_marked_letter_is_not_split(mark: str) -> None:
    assert build_anagram([f"q{mark}"], random.Random(0)) == f"Q{mark}"


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


def test_crossword_symbol_map_prioritizes_number_of_words_before_total_frequency():
    mapping = crossword_symbol_map(["AAAAAAAAAB", "AB", "B"])
    assert mapping == {"B": "#", "A": "*"}


def test_crossword_build_applies_mapping_per_word():
    result = build_crossword(["ABDUCT", "COMPUTER", "DESTROY"])
    assert result == "1. AB@U*#\n2. *OMPU#ER\n3. @ES#ROY"


def test_crossword_single_word_has_no_symbols():
    assert build_crossword(["abduct"]) == "1. ABDUCT"


@pytest.mark.parametrize("mark", ["\u0301", pytest.param("\u05c8", id="unicode18")])
def test_crossword_does_not_treat_a_shared_mark_as_a_letter(mark: str) -> None:
    assert crossword_symbol_map([f"q{mark}a", f"x{mark}b"]) == {}
    assert build_crossword([f"q{mark}a", f"x{mark}b"]) == f"1. Q{mark}A\n2. X{mark}B"


@pytest.mark.parametrize("mark", ["\u0301", pytest.param("\u05c8", id="unicode18")])
def test_crossword_substitutes_the_whole_marked_letter(mark: str) -> None:
    assert crossword_symbol_map([f"q{mark}a", f"q{mark}b"]) == {f"Q{mark}": "#"}
    assert build_crossword([f"q{mark}a", f"q{mark}b"]) == "1. #A\n2. #B"


@pytest.mark.parametrize(
    "first, second, shared",
    [("éa", "e\u0301b", "É"), ("ΐa", "ι\u0308\u0301b", "ΐ")],
)
def test_crossword_shares_canonically_equivalent_marked_letters(*, first: str, second: str, shared: str) -> None:
    assert crossword_symbol_map([first, second]) == {shared: "#"}
    assert build_crossword([first, second]) == "1. #A\n2. #B"


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


def test_word_search_raises_when_words_never_fit():
    # Growing the grid is bounded, so an rng that never produces a usable placement fails
    # with a clear error instead of looping forever.
    with pytest.raises(ValueError, match="Could not hide"):
        build_word_search(["abduct", "vault"], _NeverFits())


# --- letters whose uppercase form is longer ------------------------------------
#
# str.upper() turns "ß" into "SS" and "ﬁ" into "FI". The masker's clue gives each word's
# length, so every builder must encode exactly as many characters as the word has.


def test_anagram_keeps_letters_whose_uppercase_expands():
    sequence = build_anagram(["straße", "ﬁle"], random.Random(0))
    assert sorted(sequence) == sorted("STRAßEﬁLE")
    assert len(sequence) == len("straße") + len("ﬁle")


def test_crossword_lines_keep_the_length_of_their_words():
    lines = build_crossword(["straße", "tasse"]).splitlines()
    assert [len(line.split(". ", 1)[1]) for line in lines] == [6, 5]


def test_word_search_hides_the_word_without_expanding_it():
    grid = _parse_grid(build_word_search(["straße", "tasse"], random.Random(42)))
    assert _find_word(grid, "STRAßE")
    assert _find_word(grid, "TASSE")


def test_anagram_preserves_distinct_case_folded_words() -> None:
    sequence = build_anagram(["ılık", "ilik"], random.Random(42))
    assert sorted(sequence) == sorted("ıLıKILIK")


def test_crossword_preserves_distinct_case_folded_words() -> None:
    assert build_crossword(["ılık", "ilik"]) == "1. ı*ı#\n2. I*I#"


def test_word_search_preserves_distinct_case_folded_words() -> None:
    grid = _parse_grid(build_word_search(["ılık", "ilik"], random.Random(42)))
    assert _find_word(grid, "ıLıK")
    assert _find_word(grid, "ILIK")
