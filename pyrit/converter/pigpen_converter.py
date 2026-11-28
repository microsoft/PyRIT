# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._character_substitution_converter import _CharacterSubstitutionConverter
from pyrit.converter.text_selection_strategy import WordSelectionStrategy

# Pigpen (Freemason's / Masonic) cipher: a geometric monoalphabetic substitution of the Latin
# alphabet; see https://en.wikipedia.org/wiki/Pigpen_cipher . Framing classical ciphers as an
# LLM jailbreak vector: CipherChat (Yuan et al., 2023, arXiv:2308.06463).
#
# Each glyph is the shape the key draws around that letter's grid cell: the tic-tac-toe grid for
# A-I, the same with a dot for J-R, the saltire (X) for S-V, and the same with a dot for W-Z.
# Base glyphs are written as explicit code points so each can be checked against its Unicode name.
_DOT = "̇"  # COMBINING DOT ABOVE
_BASE_GLYPHS = {
    "a": "┘",  # ┘ BOX DRAWINGS LIGHT UP AND LEFT
    "b": "⊔",  # ⊔ SQUARE CUP
    "c": "└",  # └ BOX DRAWINGS LIGHT UP AND RIGHT
    "d": "⊐",  # ⊐ SQUARE ORIGINAL OF
    "e": "□",  # □ WHITE SQUARE
    "f": "⊏",  # ⊏ SQUARE IMAGE OF
    "g": "┐",  # ┐ BOX DRAWINGS LIGHT DOWN AND LEFT
    "h": "⊓",  # ⊓ SQUARE CAP
    "i": "┌",  # ┌ BOX DRAWINGS LIGHT DOWN AND RIGHT
    "s": "▽",  # ▽ WHITE DOWN-POINTING TRIANGLE
    "t": "▷",  # ▷ WHITE RIGHT-POINTING TRIANGLE
    "u": "◁",  # ◁ WHITE LEFT-POINTING TRIANGLE
    "v": "△",  # △ WHITE UP-POINTING TRIANGLE
}


def _build_pigpen_substitutions() -> dict[str, list[str]]:
    """
    Build the full A-Z Pigpen table: A-I/S-V as-is, J-R/W-Z the same glyphs with a combining dot.

    Returns:
        dict[str, list[str]]: The Pigpen substitution table keyed by lowercase letter.
    """
    table = {letter: [glyph] for letter, glyph in _BASE_GLYPHS.items()}
    # J-R repeat the A-I glyphs with the dot; W-Z repeat the S-V glyphs with the dot.
    table.update({chr(ord("j") + i): [_BASE_GLYPHS[chr(ord("a") + i)] + _DOT] for i in range(9)})
    table.update({chr(ord("w") + i): [_BASE_GLYPHS[chr(ord("s") + i)] + _DOT] for i in range(4)})
    return table


_PIGPEN_SUBSTITUTIONS = _build_pigpen_substitutions()


class PigpenConverter(_CharacterSubstitutionConverter):
    """
    Encodes text with the Pigpen (Freemason's / Masonic) cipher.

    Pigpen is a geometric monoalphabetic substitution: each letter becomes the fragment of the grid
    that surrounds its cell. This is the geometric member of the character-substitution family
    (see ``_CharacterSubstitutionConverter``); like Leetspeak it is case-folding and not reversible
    (``Hello`` and ``hello`` encode identically).
    """

    def __init__(self, *, word_selection_strategy: WordSelectionStrategy | None = None) -> None:
        """
        Initialize the converter.

        Args:
            word_selection_strategy (WordSelectionStrategy | None): Strategy for selecting which words
                to convert. If None, all words are converted.
        """
        super().__init__(
            substitutions=_PIGPEN_SUBSTITUTIONS,
            deterministic=True,
            word_selection_strategy=word_selection_strategy,
        )

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into Pigpen glyphs.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        return self._substitute_word(word)
