# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared building blocks for classical Polybius-square (and cube) ciphers.

Several PyRIT converters are built on the same idea: arrange an alphabet into a keyed grid
and look letters up by their coordinates. This module factors out the parts they all repeat so
each converter only has to express its own enciphering rule:

- ``build_keyed_alphabet`` — turn a keyword into a de-duplicated ordering of an alphabet
  (the "key square" / "mixed alphabet" shared by Playfair, Two-square, Four-square, Bifid and
  Trifid).
- ``sanitize_to_alphabet`` — reduce free text to the run of letters the cipher operates on,
  folding a merged letter and dropping everything outside the alphabet.
- ``PolybiusSquare`` — a thin wrapper over a square grid with letter/coordinate lookups,
  convenient for the 2-D digraph ciphers (Playfair, Two-square, Four-square).

This module is intentionally private (underscore prefix): it is an internal helper shared by the
cipher converters, not part of the public ``pyrit.converter`` API.
"""

import string

# The canonical 25-letter alphabet for a 5x5 square with I/J merged (J omitted). This is the
# usual reduction used by the 5x5 Polybius-square ciphers.
IJ_MERGED_ALPHABET = "ABCDEFGHIKLMNOPQRSTUVWXYZ"

# The full 26-letter alphabet, used when no letter is merged (e.g. Trifid's 27-symbol cube,
# which instead appends a filler symbol).
FULL_ALPHABET = string.ascii_uppercase

# A ``merge`` pair maps a letter that is absent from the grid to the letter that stands in for it
# (e.g. ``("J", "I")``): every occurrence of the first is treated as the second.
MergePair = tuple[str, str]


def build_keyed_alphabet(
    key: str,
    *,
    alphabet: str = IJ_MERGED_ALPHABET,
    merge: MergePair | None = None,
    append: str = "",
) -> list[str]:
    """
    Build an ordered "mixed alphabet" from a keyword.

    The key is upper-cased; if ``merge`` is given, any occurrence of ``merge[0]`` is folded to
    ``merge[1]``. Characters that survive and belong to ``alphabet`` are taken in order, skipping
    duplicates, then the remaining letters of ``alphabet`` are appended in their natural order.
    Finally any characters in ``append`` are appended verbatim (used for a cube filler symbol).

    Args:
        key (str): The keyword seeding the ordering. Characters outside ``alphabet`` (after the
            optional merge fold) are ignored.
        alphabet (str): The pool of symbols that fill the grid, in natural order. Defaults to the
            I/J-merged 25-letter alphabet.
        merge (MergePair | None): Optional ``(absent_letter, target_letter)`` fold applied to the
            key. ``absent_letter`` must not be in ``alphabet``; ``target_letter`` must be.
        append (str): Extra symbols appended after the alphabet (e.g. Trifid's filler). These are
            not de-duplicated against the alphabet.

    Returns:
        list[str]: The ordered symbols of the grid, length ``len(alphabet) + len(append)``.
    """
    merge_from, merge_to = merge if merge is not None else (None, None)
    alphabet_set = set(alphabet)

    ordered: list[str] = []
    seen: set[str] = set()
    for char in key.upper():
        if merge_from is not None and char == merge_from:
            char = merge_to  # type: ignore[assignment]
        if char in alphabet_set and char not in seen:
            ordered.append(char)
            seen.add(char)
    for char in alphabet:
        if char not in seen:
            ordered.append(char)
            seen.add(char)
    ordered.extend(append)
    return ordered


def sanitize_to_alphabet(
    text: str,
    *,
    alphabet_set: set[str],
    merge: MergePair | None = None,
) -> list[str]:
    """
    Reduce free text to the ordered letters a square cipher operates on.

    The text is upper-cased; if ``merge`` is given, ``merge[0]`` is folded to ``merge[1]``; then
    every character not in ``alphabet_set`` (spaces, digits, punctuation, non-ASCII) is dropped.

    Args:
        text (str): The input text.
        alphabet_set (set[str]): The set of symbols retained (typically ``set(alphabet)``).
        merge (MergePair | None): Optional ``(absent_letter, target_letter)`` fold.

    Returns:
        list[str]: The retained symbols, in order.
    """
    merge_from, merge_to = merge if merge is not None else (None, None)
    cleaned: list[str] = []
    for char in text.upper():
        if merge_from is not None and char == merge_from:
            char = merge_to  # type: ignore[assignment]
        if char in alphabet_set:
            cleaned.append(char)
    return cleaned


class PolybiusSquare:
    """
    A square grid of symbols with letter <-> coordinate lookups.

    Wraps a flat ordering (as returned by ``build_keyed_alphabet``) laid out row-major into a
    ``side`` x ``side`` grid. ``grid[row][col]`` reads a symbol by its zero-based coordinates and
    ``position[symbol]`` gives its ``(row, col)`` back. Convenient for the 2-D digraph ciphers.
    """

    def __init__(self, letters: list[str], *, side: int = 5) -> None:
        """
        Initialize the square from an ordered list of symbols.

        Args:
            letters (list[str]): The ordered symbols, length ``side * side``.
            side (int): The grid side length. Defaults to 5.

        Raises:
            ValueError: If ``len(letters)`` is not ``side * side``.
        """
        if len(letters) != side * side:
            raise ValueError(f"PolybiusSquare expects {side * side} symbols, got {len(letters)}.")
        self.side = side
        self._letters = list(letters)
        self.grid: list[list[str]] = [self._letters[row * side : (row + 1) * side] for row in range(side)]
        self.position: dict[str, tuple[int, int]] = {
            char: (index // side, index % side) for index, char in enumerate(self._letters)
        }
