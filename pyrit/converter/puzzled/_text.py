# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared letter units for PUZZLED clues and puzzle encodings."""

import unicodedata

import regex

_COMBINING_MARK_PATTERN = regex.compile(r"\p{M}")


def _is_combining_mark(char: str) -> bool:
    return _COMBINING_MARK_PATTERN.fullmatch(char) is not None


def _letter_units(word: str) -> list[str]:
    """
    Split a canonically composed word into letters with their combining marks attached.

    Mark classification uses the same Unicode properties as the word scanner.

    Args:
        word (str): The word or puzzle sequence to split.

    Returns:
        list[str]: One unit per letter, including any following combining marks.
    """
    letters: list[str] = []
    for char in unicodedata.normalize("NFC", word):
        if letters and _is_combining_mark(char):
            letters[-1] += char
        else:
            letters.append(char)
    return letters
