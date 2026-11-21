# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter import PigpenConverter

# Expected glyphs independently transcribed from the Pigpen key
# (https://en.wikipedia.org/wiki/Pigpen_cipher): each glyph is the shape the key draws around the
# letter's grid cell. A-I use the tic-tac-toe grid; J-R repeat A-I with a combining dot; S-V use
# the saltire (X); W-Z repeat S-V with a dot. Each value is annotated with its Unicode name so it
# can be checked against the key (not the rendered font), pinning the converter to the key rather
# than re-deriving it from the production table.
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


async def test_pigpen_converter_encodes_every_letter_to_the_key():
    """PigpenConverter encodes each letter to the glyph the key draws around its cell.

    The expected values are transcribed directly from the key, so a mirrored or swapped glyph
    (e.g. ``a`` drawn as the key's ``i``) fails here instead of silently encoding ``cat`` as ``git``.
    """
    converter = PigpenConverter()
    for letter, expected in _EXPECTED_PIGPEN.items():
        result = await converter.convert_async(prompt=letter)
        assert result.output_text == expected, f"{letter!r} encoded wrong"


async def test_pigpen_converter_encodes_word_to_key_glyphs():
    """A whole word encodes to the key's glyphs (and is case-folding)."""
    converter = PigpenConverter()
    # c, a, t written independently from the key: └ ┘ ▷
    assert (await converter.convert_async(prompt="cat")).output_text == "└┘▷"
    # Case-folding: Hello and hello encode identically.
    assert (await converter.convert_async(prompt="Hello")).output_text == (
        await converter.convert_async(prompt="hello")
    ).output_text


def test_pigpen_converter_is_exported_and_text_only():
    converter = PigpenConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
