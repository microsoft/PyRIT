# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import BrailleConverter, ConverterResult

# Printable ASCII symbols and their two-cell Unified English Braille sequences.
UEB_SYMBOL_CELLS = {
    "@": "\u2808\u2801",  # dot 4, dot 1
    "#": "\u2838\u2839",  # dots 456, dots 1456
    "%": "\u2828\u2834",  # dots 46, dots 356
    "&": "\u2808\u282f",  # dot 4, dots 12346
    "*": "\u2810\u2814",  # dot 5, dots 35
    "+": "\u2810\u2816",  # dot 5, dots 235
    "<": "\u2808\u2823",  # dot 4, dots 126
    "=": "\u2810\u2836",  # dot 5, dots 2356
    ">": "\u2808\u281c",  # dot 4, dots 345
    '"': "\u2820\u2836",  # dot 6, dots 2356
    "[": "\u2828\u2823",  # dots 46, dots 126
    "]": "\u2828\u281c",  # dots 46, dots 345
    "\\": "\u2838\u2821",  # dots 456, dots 16
    "^": "\u2808\u2822",  # dot 4, dots 26
    "_": "\u2828\u2824",  # dots 46, dots 36
    "`": "\u2828\u2821",  # dots 46, dots 16
    "{": "\u2838\u2823",  # dots 456, dots 126
    "}": "\u2838\u281c",  # dots 456, dots 345
    "|": "\u2838\u2833",  # dots 456, dots 1256
    "~": "\u2808\u2814",  # dot 4, dots 35
}


async def test_braille_converter_simple_text():
    """Test basic Braille conversion."""
    converter = BrailleConverter()
    prompt = "hello"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Verify it returns some braille characters
    assert result.output_text != ""
    assert result.output_text != prompt


async def test_braille_converter_with_space():
    """Test Braille conversion with spaces."""
    converter = BrailleConverter()
    prompt = "hi there"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Should preserve space
    assert " " in result.output_text


async def test_braille_converter_uppercase():
    """Test Braille conversion with uppercase letters."""
    converter = BrailleConverter()
    prompt = "Hello"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    # Should have some output
    assert len(result.output_text) > 0


async def test_braille_converter_numbers():
    """Test Braille conversion with numbers."""
    converter = BrailleConverter()
    prompt = "123"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) > 0


async def test_braille_converter_consecutive_numbers_single_prefix():
    """Test that consecutive digits get only one number prefix."""
    converter = BrailleConverter()
    num_prefix = "\u283c"  # Braille number indicator

    result = await converter.convert_async(prompt="123", input_type="text")
    # Should have exactly one number prefix for a run of consecutive digits
    assert result.output_text.count(num_prefix) == 1

    result = await converter.convert_async(prompt="1.2", input_type="text")
    # Period is a number punctuation, so "1.2" stays in number mode — one prefix
    assert result.output_text.count(num_prefix) == 1


async def test_braille_converter_punctuation():
    """Test Braille conversion with punctuation."""
    converter = BrailleConverter()
    prompt = "Hello, world!"

    result = await converter.convert_async(prompt=prompt, input_type="text")

    assert isinstance(result, ConverterResult)
    assert result.output_type == "text"
    assert len(result.output_text) > 0


async def test_braille_converter_input_type_not_supported():
    """Test that non-text input types raise ValueError."""
    converter = BrailleConverter()

    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")


def test_braille_converter_input_supported():
    """Test input_supported method."""
    converter = BrailleConverter()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False


def test_braille_converter_output_supported():
    """Test output_supported method."""
    converter = BrailleConverter()
    assert converter.output_supported("text") is True
    assert converter.output_supported("image_path") is False


async def test_braille_converter_punctuation_cells():
    """Punctuation must map to the correct English/UEB braille cells.

    The existing tests only assert the output is non-empty, so an incorrect cell
    could pass unnoticed. English Braille punctuation is standard across UEB and the
    major national codes; pin the exact cells here. Regression for the semicolon,
    which was mapped to the letter-sign cell U+2830 (dots 5,6) instead of the
    semicolon cell U+2806 (dots 2,3).
    """
    converter = BrailleConverter()
    # char -> expected single braille cell (English/UEB)
    expected = {
        ",": "⠂",  # dot 2
        ";": "⠆",  # dots 2,3
        ":": "⠒",  # dots 2,5
        ".": "⠲",  # dots 2,5,6
        "!": "⠖",  # dots 2,3,5
        "?": "⠦",  # dots 2,3,6
    }
    for char, cell in expected.items():
        result = await converter.convert_async(prompt=char, input_type="text")
        assert result.output_text == cell, (
            f"{char!r} -> {result.output_text!r} (U+{ord(result.output_text):04X}), "
            f"expected {cell!r} (U+{ord(cell):04X})"
        )


@pytest.mark.parametrize("char, expected", sorted(UEB_SYMBOL_CELLS.items()))
async def test_braille_converter_ascii_symbol_cells(char, expected):
    """Printable ASCII symbols map to their UEB cells rather than being dropped.

    Regression: '@', '%', '+', '<' and the other unmapped symbols were silently
    dropped, corrupting the encoded prompt (e.g. "a@b.com" became "ab.com").
    Cells are pinned against the Unified English Braille symbol definitions.
    """
    converter = BrailleConverter()

    result = await converter.convert_async(prompt=char, input_type="text")
    assert result.output_text == expected


@pytest.mark.parametrize(
    "char",
    ["\u00e9", "\u4e2d", "\U0001f600", "\n", "\t", "\r"],
    ids=["e-acute", "cjk", "emoji", "newline", "tab", "carriage-return"],
)
async def test_braille_converter_unmapped_characters_pass_through(char):
    """Characters with no Braille cell survive conversion unchanged."""
    converter = BrailleConverter()

    result = await converter.convert_async(prompt=f"a{char}b", input_type="text")
    assert result.output_text == f"\u2801{char}\u2803"


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("hello", "\u2813\u2811\u2807\u2807\u2815"),
        ("a@b.com", "\u2801\u2808\u2801\u2803\u2832\u2809\u2815\u280d"),
        ("1+2", "\u283c\u2801\u2810\u2816\u283c\u2803"),
        ("100%", "\u283c\u2801\u281a\u281a\u2828\u2834"),
        ("caf\u00e9", "\u2809\u2801\u280b\u00e9"),
    ],
    ids=["letters", "email", "digits-around-symbol", "percent", "accented-letter"],
)
async def test_braille_converter_exact_output(prompt, expected):
    """Mapped characters are still encoded, and pass-through does not corrupt number mode."""
    converter = BrailleConverter()

    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == expected
