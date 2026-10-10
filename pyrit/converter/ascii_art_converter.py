# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


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
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        font = self._font
        if font == "rand":
            font = self._get_random_generator(stream="font").choice(_ART_RANDOM_FONTS)

        return ConverterResult(output_text=text2art(prompt, font=font), output_type="text")
