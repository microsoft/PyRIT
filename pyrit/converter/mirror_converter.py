# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._character_map import CharacterMapConverter


class MirrorConverter(CharacterMapConverter):
    """
    Renders text mirror-reversed ("mirror writing" / Leonardo-style backwards text).

    Each character is replaced by a Unicode glyph that reads as its horizontal (left-to-right)
    reflection, and the whole string is then reversed, so the result looks like the original text
    held up to a mirror. For example, ``"be"`` becomes ``"ɘd"``.

    The mirrored glyphs are drawn from standard Unicode blocks -- mainly IPA Extensions
    (U+0250-U+02AF), Latin Extended-B (U+0180-U+024F), Latin Extended-D (U+A720-U+A7FF),
    Supplemental Punctuation (U+2E00-U+2E7F) and a few Greek/Cyrillic look-alikes -- chosen because
    their shapes read as the horizontal reflection of the source character (e.g. ``b``/``d``,
    ``p``/``q``, ``e``->``ɘ``, ``R``->``Я``, ``?``->``⸮``). Paired brackets are swapped (``(``<->``)``)
    so that after the string is reversed they end up oriented correctly.

    Characters with no sensible mirrored glyph -- and characters that are already symmetric about a
    vertical axis (e.g. ``A H I M O T U V W X Y o i l m n u v w x``, spaces, most digits) -- are left
    unchanged; they already mirror to themselves. Because many characters are passed through, this is
    a lossy, approximate transform rather than a reversible encoding.

    This is a folk/typographic transform with no formal academic source; it mirrors the "mirror text"
    generators common online and the ``mirror`` style in elder-plinius's P4RS3LT0NGV3 repository.
    """

    _REVERSE_OUTPUT = True

    # Map of source character -> glyph that reads as its horizontal (left-right) reflection.
    # Symmetric characters (A H I M O T U V W X Y o i l m n u v w x, most digits, spaces) are
    # intentionally absent: they mirror to themselves and are left unchanged.
    _MIRROR_MAP = {
        # Lowercase letters
        "a": "ɒ",  # ɒ LATIN SMALL LETTER TURNED ALPHA
        "b": "d",
        "c": "ɔ",  # ɔ LATIN SMALL LETTER OPEN O
        "d": "b",
        "e": "ɘ",  # ɘ LATIN SMALL LETTER REVERSED E
        "p": "q",
        "q": "p",
        "r": "ɿ",  # ɿ LATIN SMALL LETTER REVERSED R WITH FISHHOOK
        "s": "ƨ",  # ƨ LATIN SMALL LETTER TONE TWO
        "z": "ƹ",  # ƹ LATIN SMALL LETTER EZH REVERSED
        # Uppercase letters
        "C": "Ɔ",  # Ɔ LATIN CAPITAL LETTER OPEN O
        "E": "Ǝ",  # Ǝ LATIN CAPITAL LETTER REVERSED E
        "F": "ꟻ",  # ꟻ LATIN EPIGRAPHIC LETTER REVERSED F
        "L": "⅃",  # ⅃ REVERSED SANS-SERIF CAPITAL L
        "N": "И",  # И CYRILLIC CAPITAL LETTER I (reversed N)
        "R": "Я",  # Я CYRILLIC CAPITAL LETTER YA (reversed R)
        "S": "Ƨ",  # Ƨ LATIN CAPITAL LETTER TONE TWO
        # Digits
        "3": "Ɛ",  # Ɛ LATIN CAPITAL LETTER OPEN E (mirrored 3)
        # Punctuation (brackets are swapped so they read correctly after the string is reversed)
        "(": ")",
        ")": "(",
        "[": "]",
        "]": "[",
        "{": "}",
        "}": "{",
        "<": ">",
        ">": "<",
        "/": "\\",
        "\\": "/",
        "?": "⸮",  # ⸮ REVERSED QUESTION MARK
    }
    _CHAR_MAP = _MIRROR_MAP
