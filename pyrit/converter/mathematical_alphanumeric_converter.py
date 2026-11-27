# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any, ClassVar

from pyrit.converter.text_selection_strategy import WordSelectionStrategy
from pyrit.converter.word_level_converter import WordLevelConverter
from pyrit.models import ComponentIdentifier

# Base code points for each style's A-Z, a-z, and (where defined) 0-9 runs inside the
# Unicode Mathematical Alphanumeric Symbols block (U+1D400-U+1D7FF). Each run is contiguous
# except for the reserved code points handled by ``_STYLE_EXCEPTIONS`` below.
_STYLE_BASES: dict[str, dict[str, int]] = {
    "bold": {"upper": 0x1D400, "lower": 0x1D41A, "digit": 0x1D7CE},
    "italic": {"upper": 0x1D434, "lower": 0x1D44E},
    "bold_italic": {"upper": 0x1D468, "lower": 0x1D482},
    "script": {"upper": 0x1D49C, "lower": 0x1D4B6},
    "bold_script": {"upper": 0x1D4D0, "lower": 0x1D4EA},
    "fraktur": {"upper": 0x1D504, "lower": 0x1D51E},
    "bold_fraktur": {"upper": 0x1D56C, "lower": 0x1D586},
    "double_struck": {"upper": 0x1D538, "lower": 0x1D552, "digit": 0x1D7D8},
    "sans_serif": {"upper": 0x1D5A0, "lower": 0x1D5BA, "digit": 0x1D7E2},
    "sans_serif_bold": {"upper": 0x1D5D4, "lower": 0x1D5EE, "digit": 0x1D7EC},
    "sans_serif_italic": {"upper": 0x1D608, "lower": 0x1D622},
    "sans_serif_bold_italic": {"upper": 0x1D63C, "lower": 0x1D656},
    "monospace": {"upper": 0x1D670, "lower": 0x1D68A, "digit": 0x1D7F6},
}

# Reserved-codepoint exceptions. A handful of styled letters are "holes" in the U+1D400 block
# because the glyphs were already encoded in the Letterlike Symbols block (U+2100-U+214F).
# The naive ``base + offset`` arithmetic lands on an unassigned code point for these, so each
# must be mapped to its real glyph instead. Styles not listed here (bold, bold_italic,
# bold_script, bold_fraktur, every sans-serif variant, monospace) have fully contiguous runs.
_STYLE_EXCEPTIONS: dict[str, dict[str, str]] = {
    "italic": {
        "h": "ℎ",  # PLANCK CONSTANT
    },
    "script": {
        "B": "ℬ",  # SCRIPT CAPITAL B
        "E": "ℰ",  # SCRIPT CAPITAL E
        "F": "ℱ",  # SCRIPT CAPITAL F
        "H": "ℋ",  # SCRIPT CAPITAL H
        "I": "ℐ",  # SCRIPT CAPITAL I
        "L": "ℒ",  # SCRIPT CAPITAL L
        "M": "ℳ",  # SCRIPT CAPITAL M
        "R": "ℛ",  # SCRIPT CAPITAL R
        "e": "ℯ",  # SCRIPT SMALL E
        "g": "ℊ",  # SCRIPT SMALL G
        "o": "ℴ",  # SCRIPT SMALL O
    },
    "fraktur": {
        "C": "ℭ",  # BLACK-LETTER CAPITAL C
        "H": "ℌ",  # BLACK-LETTER CAPITAL H
        "I": "ℑ",  # BLACK-LETTER CAPITAL I
        "R": "ℜ",  # BLACK-LETTER CAPITAL R
        "Z": "ℨ",  # BLACK-LETTER CAPITAL Z
    },
    "double_struck": {
        "C": "ℂ",  # DOUBLE-STRUCK CAPITAL C
        "H": "ℍ",  # DOUBLE-STRUCK CAPITAL H
        "N": "ℕ",  # DOUBLE-STRUCK CAPITAL N
        "P": "ℙ",  # DOUBLE-STRUCK CAPITAL P
        "Q": "ℚ",  # DOUBLE-STRUCK CAPITAL Q
        "R": "ℝ",  # DOUBLE-STRUCK CAPITAL R
        "Z": "ℤ",  # DOUBLE-STRUCK CAPITAL Z
    },
}


def _build_style_map(style: str) -> dict[str, str]:
    """
    Build the ASCII-to-styled character map for a single style.

    Args:
        style (str): The style name (a key of ``_STYLE_BASES``).

    Returns:
        dict[str, str]: Mapping from each ASCII letter/digit to its styled glyph.
    """
    bases = _STYLE_BASES[style]
    exceptions = _STYLE_EXCEPTIONS.get(style, {})
    char_map: dict[str, str] = {}
    for offset in range(26):
        upper = chr(ord("A") + offset)
        lower = chr(ord("a") + offset)
        char_map[upper] = exceptions.get(upper, chr(bases["upper"] + offset))
        char_map[lower] = exceptions.get(lower, chr(bases["lower"] + offset))
    if "digit" in bases:
        for offset in range(10):
            digit = chr(ord("0") + offset)
            char_map[digit] = chr(bases["digit"] + offset)
    return char_map


class MathematicalAlphanumericConverter(WordLevelConverter):
    """
    Converts ASCII letters and digits to styled glyphs from the Unicode Mathematical
    Alphanumeric Symbols block (U+1D400-U+1D7FF).

    The block provides a full set of Latin letters (and, for some styles, digits) rendered in
    a selectable typographic style: bold, italic, script, fraktur, double-struck, sans-serif,
    monospace, and bold/italic combinations of these. For example, with ``style="bold"``,
    "Hello" becomes "𝐇𝐞𝐥𝐥𝐨"; with ``style="fraktur"`` it becomes "ℌ𝔢𝔩𝔩𝔬".

    A handful of styled letters are reserved ("holes") in the block because their glyphs were
    already encoded in the Letterlike Symbols block (U+2100-U+214F) — e.g. the script/fraktur
    capitals ℋ, ℌ and the double-struck ℍ. These are mapped to the correct glyph so no
    unassigned code point is ever emitted. Characters with no styled form (punctuation, spaces,
    digits for styles that define none, non-ASCII) are left unchanged.

    This is a filter-evasion / typographic-obfuscation style transform: the output is visually
    close to the original but uses entirely different code points, which can slip past filters
    or classifiers that match on exact ASCII without first normalizing the input. There is no
    formal academic jailbreak source; it is a well-known Unicode obfuscation technique.
    """

    #: Style names accepted by the ``style`` parameter, each precomputed into a character map.
    _STYLE_MAPS: ClassVar[dict[str, dict[str, str]]] = {style: _build_style_map(style) for style in _STYLE_BASES}

    def __init__(
        self,
        *,
        style: str = "bold",
        word_selection_strategy: WordSelectionStrategy | None = None,
        word_split_separator: str | None = " ",
        **kwargs: Any,
    ) -> None:
        """
        Initialize the converter with the chosen typographic style.

        Args:
            style (str): The Mathematical Alphanumeric style to apply. One of: ``bold``,
                ``italic``, ``bold_italic``, ``script``, ``bold_script``, ``fraktur``,
                ``bold_fraktur``, ``double_struck``, ``sans_serif``, ``sans_serif_bold``,
                ``sans_serif_italic``, ``sans_serif_bold_italic``, ``monospace``. Defaults to
                ``bold``.
            word_selection_strategy (WordSelectionStrategy | None): Strategy for selecting which
                words to convert. If None, all words are converted. Defaults to None.
            word_split_separator (str | None): Separator used to split words in the input text.
                If None, splits by any whitespace. Defaults to " ".
            **kwargs: Forwarded to ``WordLevelConverter.__init__``.

        Raises:
            ValueError: If ``style`` is not a recognized style name.
        """
        super().__init__(
            word_selection_strategy=word_selection_strategy,
            word_split_separator=word_split_separator,
            **kwargs,
        )
        if style not in self._STYLE_MAPS:
            supported = ", ".join(sorted(self._STYLE_MAPS))
            raise ValueError(f"Unsupported style '{style}'. Supported styles: {supported}.")
        self._style = style
        self._char_map = self._STYLE_MAPS[style]

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier including the selected style alongside word-level parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "style": self._style,
                "word_selection_strategy": self._word_selection_strategy.__class__.__name__,
                "word_selection_strategy_params": self._word_selection_strategy.get_identifier_params(),
                "word_split_separator": self._word_split_separator,
            }
        )

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into its styled Mathematical Alphanumeric form.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The word with ASCII letters/digits replaced by styled glyphs; other
            characters are left unchanged.
        """
        return "".join(self._char_map.get(char, char) for char in word)
