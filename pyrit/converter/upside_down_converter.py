# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._character_map import CharacterMapConverter


class UpsideDownConverter(CharacterMapConverter):
    """
    Renders text "upside down" by mapping each character to its rotated-180 degree Unicode
    look-alike and reversing the whole string, so the result reads as the original text flipped
    on the page (e.g. "Hello" -> "ollǝH").

    This is a folk/typographic transform with **no formal academic source**. It mirrors the
    "upside-down text" generators that are widely available online (fliptext.org,
    upsidedowntext.com, etc.) and the ``upside_down`` style popularized by elder-plinius'
    P4RS3LT0NGV3 collection. As an obfuscation it is useful for probing whether a filter or
    classifier normalizes visually-rotated Unicode before matching: the glyphs are visually
    legible to a human but have entirely different codepoints from the ASCII source.

    The substitution table is hand-curated from the standard rotated glyphs these generators
    use. They are drawn from several Unicode blocks, including:

    - Latin Extended-B, IPA Extensions and Spacing Modifier Letters for the turned lowercase
      letters (e.g. U+0250 LATIN SMALL LETTER TURNED A, U+01DD LATIN SMALL LETTER TURNED E,
      U+026F LATIN SMALL LETTER TURNED M, U+0287 LATIN SMALL LETTER TURNED T).
    - Latin Extended-D (U+A781 LATIN SMALL LETTER TURNED L) and Phonetic Extensions
      (U+1D09 LATIN SMALL LETTER TURNED I, U+1D1A LATIN LETTER SMALL CAPITAL TURNED R).
    - Letterlike Symbols and Mathematical Operators for turned capitals and symbols
      (U+2132 TURNED CAPITAL F, U+2141 TURNED SANS-SERIF CAPITAL G, U+2142 TURNED SANS-SERIF
      CAPITAL L, U+2144 TURNED SANS-SERIF CAPITAL Y, U+2200 FOR ALL used for 'A', U+22A5 UP
      TACK used for 'T', U+2229 INTERSECTION used for 'U', U+214B TURNED AMPERSAND).
    - Latin-1 Supplement for inverted punctuation (U+00A1 INVERTED EXCLAMATION MARK,
      U+00BF INVERTED QUESTION MARK) plus a handful of CJK/Hangul/Bopomofo glyphs reused by
      the generators for digits that lack a dedicated rotated form.

    Characters with no entry in the table (spaces, most symbols, and any non-ASCII input) are
    left unchanged, though their position is still reversed along with the rest of the string.
    The transform is intentionally lossy and is not guaranteed to round-trip: several source
    characters are rotationally symmetric (e.g. 'o', 'H', 'N', '0', '8') and map to themselves.
    """

    _REVERSE_OUTPUT = True

    # Rotated-180-degree look-alikes keyed by the upright source character. Characters that
    # are (near) symmetric under rotation -- "o", "s", "x", "z", "H", "I", "N", "O", "S", "X",
    # "Z", "0", "1", "8", and most whitespace -- are deliberately omitted so they map to
    # themselves. Brackets/quotes swap with their mirror partner so that, after the whole
    # string is reversed, an enclosing pair still reads correctly upside down.
    _UPSIDE_DOWN_MAP = {
        # lowercase a-z
        "a": "ɐ",  # LATIN SMALL LETTER TURNED A
        "b": "q",
        "c": "ɔ",  # LATIN SMALL LETTER OPEN O
        "d": "p",
        "e": "ǝ",  # LATIN SMALL LETTER TURNED E
        "f": "ɟ",  # LATIN SMALL LETTER DOTLESS J WITH STROKE
        "g": "ƃ",  # LATIN SMALL LETTER B WITH TOPBAR
        "h": "ɥ",  # LATIN SMALL LETTER TURNED H
        "i": "ᴉ",  # LATIN SMALL LETTER TURNED I
        "j": "ɾ",  # LATIN SMALL LETTER R WITH FISHHOOK
        "k": "ʞ",  # LATIN SMALL LETTER TURNED K
        "l": "ꞁ",  # LATIN SMALL LETTER TURNED L
        "m": "ɯ",  # LATIN SMALL LETTER TURNED M
        "n": "u",
        "p": "d",
        "q": "b",
        "r": "ɹ",  # LATIN SMALL LETTER TURNED R
        "t": "ʇ",  # LATIN SMALL LETTER TURNED T
        "u": "n",
        "v": "ʌ",  # LATIN SMALL LETTER TURNED V
        "w": "ʍ",  # LATIN SMALL LETTER TURNED W
        "y": "ʎ",  # LATIN SMALL LETTER TURNED Y
        # uppercase A-Z (letters without a clean rotated glyph are left as-is)
        "A": "∀",  # FOR ALL
        "C": "Ɔ",  # LATIN CAPITAL LETTER OPEN O
        "E": "Ǝ",  # LATIN CAPITAL LETTER REVERSED E
        "F": "Ⅎ",  # TURNED CAPITAL F
        "G": "⅁",  # TURNED SANS-SERIF CAPITAL G
        "J": "ſ",  # LATIN SMALL LETTER LONG S
        "K": "⋊",  # RIGHT NORMAL FACTOR SEMIDIRECT PRODUCT
        "L": "⅂",  # TURNED SANS-SERIF CAPITAL L
        "M": "W",
        "P": "Ԁ",  # CYRILLIC CAPITAL LETTER KOMI DE
        "R": "ᴚ",  # LATIN LETTER SMALL CAPITAL TURNED R
        "T": "⊥",  # UP TACK
        "U": "∩",  # INTERSECTION
        "V": "Λ",  # GREEK CAPITAL LETTER LAMDA
        "W": "M",
        "Y": "⅄",  # TURNED SANS-SERIF CAPITAL Y
        # digits (0, 1, 8 are near-symmetric and left unchanged)
        "2": "ᄅ",  # HANGUL CHOSEONG RIEUL
        "3": "Ɛ",  # LATIN CAPITAL LETTER OPEN E
        "4": "ᔭ",  # CANADIAN SYLLABICS YA
        "5": "ϛ",  # GREEK SMALL LETTER STIGMA
        "6": "9",
        "7": "ㄥ",  # BOPOMOFO LETTER ENG
        "9": "6",
        # punctuation
        ".": "˙",  # DOT ABOVE
        ",": "'",
        "'": ",",
        '"': "„",  # DOUBLE LOW-9 QUOTATION MARK
        "!": "¡",  # INVERTED EXCLAMATION MARK
        "?": "¿",  # INVERTED QUESTION MARK
        "(": ")",
        ")": "(",
        "[": "]",
        "]": "[",
        "{": "}",
        "}": "{",
        "<": ">",
        ">": "<",
        "&": "⅋",  # TURNED AMPERSAND
        "_": "‾",  # OVERLINE
        ";": "؛",  # ARABIC SEMICOLON
    }
    _CHAR_MAP = _UPSIDE_DOWN_MAP
