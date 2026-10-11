# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from collections.abc import Sequence
from functools import lru_cache

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


# Bounded so prompts with many distinct unrenderable characters cannot grow the
# cache without limit. Printable ASCII tops out near 34k entries across the font
# pool, so ASCII workloads never evict.
@lru_cache(maxsize=100_000)
def _font_renders_character(character: str, font: str) -> bool:
    """
    Return whether ``font`` has a glyph for ``character``.

    ``text2art`` silently omits characters without a glyph, so renderability
    is detected by rendering the single character and checking for any
    non-whitespace output. Only ``" "`` and ``"\\n"`` are layout for
    ``text2art``; other whitespace (tab, carriage return, no-break space)
    has no glyph and would be dropped, so it goes through the same probe.

    Args:
        character (str): The character to check.
        font (str): The art font to render with.

    Returns:
        bool: True if the font renders the character, or it is a space or newline.
    """
    if character in " \n":
        return True
    return bool(text2art(character, font=font).strip())


def _fonts_that_render(text: str, fonts: Sequence[str]) -> list[str]:
    """
    Return the fonts of ``fonts`` that render every character in ``text``.

    Args:
        text (str): The text the font must render fully.
        fonts (Sequence[str]): The font names to filter.

    Returns:
        list[str]: The font names whose glyphs cover every character of ``text``.
    """
    characters = set(text)
    return [font for font in fonts if all(_font_renders_character(character, font) for character in characters)]


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
            ValueError: If the input type is not supported, if the prompt contains
                characters the selected font has no glyph for, or if no font of the
                randomized font pool can render the prompt. Such characters would
                otherwise be silently dropped from the converted prompt.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        font = self._font
        if font == "rand":
            # Pool fonts freely use blank glyphs, e.g. for punctuation, so a
            # random pick can fail to render a prompt on some draws only.
            # Choose among the fonts that render the whole prompt instead.
            candidates = _fonts_that_render(prompt, _ART_RANDOM_FONTS)
            if not candidates:
                unrenderable = [
                    character
                    for character in dict.fromkeys(prompt)
                    if not any(_font_renders_character(character, pool_font) for pool_font in _ART_RANDOM_FONTS)
                ]
                characters = "".join(sorted(unrenderable))
                raise ValueError(
                    f"No font in the randomized font pool renders {len(unrenderable)} character(s) of the "
                    f"prompt: {characters!r}. They would be silently dropped from the converted prompt; "
                    "remove them or transliterate them."
                )
            font = self._get_random_generator(stream="font").choice(candidates)

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
