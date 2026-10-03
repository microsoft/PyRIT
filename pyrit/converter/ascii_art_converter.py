# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from functools import cache

from art import ASCII_FONTS, text2art

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

# These public ASCII fonts are excluded by art 6.5's built-in "rand" mode.
_ART_RANDOM_EXCLUDED_ASCII_FONTS = {
    "5x8",
    "binary",
    "decimal",
    "dwhistled",
    "flyn_sh",
    "gauntlet",
    "high_noo",
    "hills",
    "katakana",
    "morse",
    "moscow",
    "nfi1",
    "octal",
    "rot13",
    "smtengwar",
    "tengwar",
    "tsalagi",
}
_ART_RANDOM_FONTS = sorted(set(ASCII_FONTS) - _ART_RANDOM_EXCLUDED_ASCII_FONTS)


@cache
def _font_renders_character(character: str, font: str) -> bool:
    """
    Return whether ``font`` has a glyph for ``character``.

    ``text2art`` silently omits characters without a glyph, so renderability
    is detected by rendering the single character and checking for any
    non-whitespace output. Whitespace characters are layout for ``text2art``
    and always pass.

    Args:
        character (str): The character to check.
        font (str): The art font to render with.

    Returns:
        bool: True if the font renders the character or it is whitespace.
    """
    if character.isspace():
        return True
    return bool(text2art(character, font=font).strip())


class AsciiArtConverter(Converter):
    """
    Uses the `art` package to convert text into ASCII art.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, font: str = "rand") -> None:
        """
        Initialize the converter with a specified font.

        Args:
            font (str): The font to use for ASCII art. Defaults to "rand" which selects a random font.
        """
        self._font = font

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with font parameter.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "font": self._font,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into ASCII art.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the ASCII art representation of the prompt.

        Raises:
            ValueError: If the input type is not supported, or if the prompt contains
                characters the selected font has no glyph for. Such characters would
                otherwise be silently dropped from the converted prompt.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        font = self._font
        if font == "rand":
            font = self._get_random_generator(stream="font").choice(_ART_RANDOM_FONTS)

        unrenderable = [char for char in prompt if not _font_renders_character(char, font)]
        if unrenderable:
            characters = "".join(sorted(set(unrenderable)))
            raise ValueError(
                f"Cannot convert {len(unrenderable)} character(s) to ASCII art with font {font!r}: "
                f"{characters!r}. The font has no glyph for them and they would be silently "
                f"dropped from the converted prompt; remove them, transliterate them, or pick "
                f"another font."
            )

        return ConverterResult(output_text=text2art(prompt, font=font), output_type="text")
