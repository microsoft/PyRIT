# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deterministic puzzle builders for the PUZZLED jailbreak technique
(Ahn & Lee, "PUZZLED: Jailbreaking LLMs through Word-Based Puzzles", arXiv:2508.01306).

Each builder hides a list of masked keywords inside a word puzzle that a capable model
can solve. All randomness is supplied by a caller-provided ``random.Random`` instance, so
output is fully reproducible and unit-testable.
"""

import random
import string
from collections import Counter
from enum import Enum

from pyrit.converter.puzzled._text import _letter_units

# Eight placement directions for the word search: right, left, down, up, and the four
# diagonals. Each is a (row_delta, col_delta) step.
_DIRECTIONS = (
    (0, 1),
    (0, -1),
    (1, 0),
    (-1, 0),
    (1, 1),
    (1, -1),
    (-1, 1),
    (-1, -1),
)

# Symbols used by the crossword to mask the most-shared letters, in priority order.
_CROSSWORD_SYMBOLS = ("#", "*", "@")

# How many placement attempts to make per word before growing the grid.
_MAX_PLACEMENT_ATTEMPTS = 200

# How many shuffles to try before using a deterministic non-identity permutation.
_MAX_ANAGRAM_SHUFFLES = 100

# How many times the grid may grow before the build is abandoned. The paper's word-search
# algorithm (Ahn & Lee, arXiv:2508.01306, Appendix A.3) is likewise a bounded retry loop
# that raises when placement keeps failing, rather than an unbounded one.
_MAX_GRID_GROWTHS = 25


class PuzzleType(str, Enum):
    """The three puzzle encodings described in the PUZZLED paper."""

    WORD_SEARCH = "word_search"
    ANAGRAM = "anagram"
    CROSSWORD = "crossword"

    @property
    def label(self) -> str:
        """The puzzle's name in prose, for use in the text sent to the target."""
        return self.value.replace("_", " ")


def _upper_same_length(word: str) -> str:
    """
    Uppercase ``word`` without changing its length or case-folded spelling.

    ``str.upper`` expands some letters ("ß" becomes "SS", "ﬁ" becomes "FI"), which would make
    an encoded word longer than its clue. It also changes the case-folded spelling of dotless
    "ı" to "i", merging distinct words. Keep a character unchanged when either would happen.
    Combining marks are retained, even when uppercasing would turn one into a letter.

    Args:
        word (str): The word to uppercase.

    Returns:
        str: The uppercased word, preserving its length and case-folded spelling.
    """
    return "".join(
        upper if char.isalpha() and len(upper := char.upper()) == 1 and upper.casefold() == char.casefold() else char
        for char in word
    )


def _normalize(words: list[str]) -> list[list[str]]:
    """
    Uppercase the words and split them into canonically composed letter units.

    Uppercasing keeps each word's length and case-folded spelling (see ``_upper_same_length``),
    and combining marks stay attached to their letters. Every puzzle uses the same units as
    the masker's length clue.

    Args:
        words (list[str]): The masked keywords to encode.

    Returns:
        list[list[str]]: Cleaned words, each split into letters with attached marks.

    Raises:
        ValueError: If ``words`` is empty or any word has no letters after cleaning.
    """
    if not words:
        raise ValueError("At least one word is required to build a puzzle.")
    cleaned = [[_upper_same_length(letter) for letter in _letter_units(w.strip())] for w in words]
    if any(not w for w in cleaned):
        raise ValueError("Words must contain at least one non-whitespace character.")
    return cleaned


def build_anagram(words: list[str], rng: random.Random) -> str:
    """
    Concatenate the masked words and shuffle their letter units into one sequence.

    Combining marks move with their letters. The model must both unscramble and re-segment
    the sequence into the original words.

    Args:
        words (list[str]): The masked keywords to encode.
        rng (random.Random): Randomness source (injected for reproducibility).

    Returns:
        str: A single scrambled, uppercased letter sequence.

    Raises:
        ValueError: If ``words`` is empty or any word has no letters after cleaning.
    """
    letters = [letter for word in _normalize(words) for letter in word]
    source = "".join(letters)
    if len(letters) <= 1 or len(set(letters)) == 1:
        return source

    for _ in range(_MAX_ANAGRAM_SHUFFLES):
        rng.shuffle(letters)
        result = "".join(letters)
        if result != source:
            return result
    return "".join(letters[1:] + letters[:1])


def crossword_symbol_map(words: list[str]) -> dict[str, str]:
    """
    Choose which letters to mask in the crossword and map them to symbols.

    A letter is eligible when it appears in at least two different words (the shared
    intersections a solver uses to deduce the mapping). Eligible letters rank first by
    how many words contain them, then by total frequency. The top three are mapped to
    ``#``, ``*`` and ``@``; remaining ties break alphabetically for determinism.

    Args:
        words (list[str]): The masked keywords to encode.

    Returns:
        dict[str, str]: Mapping from an uppercase letter to its replacement symbol.

    Raises:
        ValueError: If ``words`` is empty or any word has no letters after cleaning.
    """
    normalized = _normalize(words)
    words_per_letter: Counter[str] = Counter()
    for word in normalized:
        for letter in set(word):
            words_per_letter[letter] += 1
    total_frequency = Counter(letter for word in normalized for letter in word)

    shared = [letter for letter, count in words_per_letter.items() if count >= 2]
    shared.sort(key=lambda letter: (-words_per_letter[letter], -total_frequency[letter], letter))

    return {letter: _CROSSWORD_SYMBOLS[i] for i, letter in enumerate(shared[: len(_CROSSWORD_SYMBOLS)])}


def build_crossword(words: list[str]) -> str:
    """
    Replace shared letters with symbols so solving one word cascades to the others.

    This encoding is a pure function of the words (no randomness): the shared letters are
    swapped for symbols and every word is listed on its own numbered line. The symbol
    legend is intentionally withheld so the model must deduce it from the clues.

    Args:
        words (list[str]): The masked keywords to encode.

    Returns:
        str: One numbered line per word with shared letters replaced by symbols.

    Raises:
        ValueError: If ``words`` is empty or any word has no letters after cleaning.
    """
    normalized = _normalize(words)
    mapping = crossword_symbol_map(words)
    lines = []
    for index, word in enumerate(normalized, start=1):
        masked = "".join(mapping.get(letter, letter) for letter in word)
        lines.append(f"{index}. {masked}")
    return "\n".join(lines)


def _grid_size(words: list[list[str]]) -> int:
    """
    Pick a square grid size large enough to place all words with room to spare.

    Args:
        words (list[list[str]]): The masked keywords split into letter units.

    Returns:
        int: The side length of the square grid.
    """
    longest = max(len(word) for word in words)
    # The paper's sizing rule, max(longest + 5, |words| * longest / 2), which fits the
    # longest word with margin and scales with the number of words so placement does not
    # thrash (Ahn & Lee, arXiv:2508.01306, Appendix A.3). Rounded up so the second term is
    # never short by a cell.
    return max(longest + 5, (len(words) * longest + 1) // 2)


def _try_place(
    grid: list[list[str]],
    word: list[str],
    rng: random.Random,
) -> bool:
    """
    Attempt to place a single word into the grid in a random position and direction.

    Overlaps are allowed only where the existing cell already holds the same letter.

    Args:
        grid (list[list[str]]): The mutable grid; empty cells are the empty string.
        word (list[str]): The letter units to place, one per cell.
        rng (random.Random): Randomness source.

    Returns:
        bool: True if the word was placed, False if no attempt fit.
    """
    size = len(grid)
    for _ in range(_MAX_PLACEMENT_ATTEMPTS):
        row_delta, col_delta = rng.choice(_DIRECTIONS)
        row = rng.randrange(size)
        col = rng.randrange(size)

        end_row = row + row_delta * (len(word) - 1)
        end_col = col + col_delta * (len(word) - 1)
        if not (0 <= end_row < size and 0 <= end_col < size):
            continue

        fits = True
        for offset, letter in enumerate(word):
            cell = grid[row + row_delta * offset][col + col_delta * offset]
            if cell not in ("", letter):
                fits = False
                break
        if not fits:
            continue

        for offset, letter in enumerate(word):
            grid[row + row_delta * offset][col + col_delta * offset] = letter
        return True
    return False


def build_word_search(words: list[str], rng: random.Random) -> str:
    """
    Hide the masked words in a square grid, then fill the gaps with random letters.

    Words may run in any of eight directions and may overlap on matching letters. If the
    words cannot all be placed, the grid grows by one and the attempt restarts, up to
    ``_MAX_GRID_GROWTHS`` times.

    Args:
        words (list[str]): The masked keywords to encode.
        rng (random.Random): Randomness source (injected for reproducibility).

    Returns:
        str: The grid, one row per line with letter units separated by spaces. A cell may
            include combining marks attached to its letter.

    Raises:
        ValueError: If ``words`` is empty, any word has no letters after cleaning, or the
            words still cannot be placed after the grid has grown the maximum number of times.
    """
    normalized = _normalize(words)
    size = _grid_size(normalized)
    # Longest words first: they are the hardest to fit, so place them while the grid is
    # still empty.
    by_length = sorted(normalized, key=len, reverse=True)

    for _ in range(_MAX_GRID_GROWTHS + 1):
        grid = [["" for _ in range(size)] for _ in range(size)]
        if all(_try_place(grid, word, rng) for word in by_length):
            break
        size += 1
    else:
        raise ValueError(
            f"Could not hide {len(normalized)} words in a word search; placement still failed after "
            f"growing the grid {_MAX_GRID_GROWTHS} times."
        )

    for row in range(size):
        for col in range(size):
            if grid[row][col] == "":
                grid[row][col] = rng.choice(string.ascii_uppercase)

    return "\n".join(" ".join(row) for row in grid)
