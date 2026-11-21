# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter import LeetspeakConverter
from pyrit.scenario.scenarios.garak.encoding import _PIGPEN_SUBSTITUTIONS

# Expected glyphs independently transcribed from the Pigpen key
# (https://en.wikipedia.org/wiki/Pigpen_cipher): each glyph is the shape the key draws around the
# letter's grid cell. A-I use the tic-tac-toe grid; J-R repeat A-I with a combining dot; S-V use
# the saltire (X); W-Z repeat S-V with a dot. Each value is annotated with its Unicode name so it
# can be checked against the key (not the rendered font), pinning the production table to the key
# rather than re-deriving it from the same source.
_DOT = "̇"  # COMBINING DOT ABOVE
_EXPECTED_PIGPEN = {
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
# J-R are the A-I glyphs with the combining dot; W-Z are the S-V glyphs with the dot.
_EXPECTED_PIGPEN.update({chr(ord("j") + k): _EXPECTED_PIGPEN[chr(ord("a") + k)] + _DOT for k in range(9)})
_EXPECTED_PIGPEN.update({chr(ord("w") + k): _EXPECTED_PIGPEN[chr(ord("s") + k)] + _DOT for k in range(4)})


def test_pigpen_table_matches_the_key():
    """Every letter maps to the glyph the Pigpen key draws around its cell.

    The expected values are transcribed directly from the key, so a mirrored or swapped glyph
    (e.g. ``a`` drawn as the key's ``i``) fails here instead of silently encoding ``cat`` as ``git``.
    """
    actual = {letter: glyphs[0] for letter, glyphs in _PIGPEN_SUBSTITUTIONS.items()}
    assert actual == _EXPECTED_PIGPEN


async def test_pigpen_technique_encodes_to_key_glyphs():
    """The pigpen technique (a LeetspeakConverter config) encodes to the key's glyphs."""
    converter = LeetspeakConverter(custom_substitutions=_PIGPEN_SUBSTITUTIONS)
    result = await converter.convert_async(prompt="cat")
    # c, a, t written independently from the key: └ ┘ ▷
    assert result.output_text == "└┘▷"
