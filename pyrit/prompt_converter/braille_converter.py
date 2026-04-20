# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class BrailleConverter(PromptConverter):
    """
    Converts text into Braille Unicode representation.

    This converter transforms standard text into Braille patterns using Unicode
    Braille characters (U+2800 to U+28FF). It supports lowercase and uppercase
    letters, numbers, common punctuation, and spaces. Uppercase letters are
    prefixed with the Braille capitalization indicator.

    The Braille mapping is based on the implementation from Garak:
    https://github.com/NVIDIA/garak/blob/main/garak/probes/encoding.py

    Note: This converter is useful for testing how AI systems handle Braille-encoded
    text, which can be used to obfuscate potentially harmful content.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given text into Braille Unicode representation.

        Args:
            prompt (str): The text to be converted to Braille.
            input_type (PromptDataType, optional): Type of input data. Defaults to "text".

        Returns:
            ConverterResult: The text converted to Braille Unicode characters.

        Raises:
            ValueError: If the input type is not supported (only "text" is supported).
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        brail_text = self._get_braile(prompt)

        return ConverterResult(output_text=brail_text, output_type="text")

    def _get_braile(self, text: str) -> str:
        """
        Retrieve the braille representation of the input text.

        Taken from Garak https://github.com/NVIDIA/garak/blob/main/garak/probes/encoding.py

        Args:
            text (str): The input text to be converted to braille.

        Returns:
            str: The braille representation of the input text.
        """
        characterUnicodes = {
            "a": "\u2801",
            "b": "\u2803",
            "k": "\u2805",
            "l": "\u2807",
            "c": "\u2809",
            "i": "\u280a",
            "f": "\u280b",
            "m": "\u280d",
            "s": "\u280e",
            "p": "\u280f",
            "e": "\u2811",
            "h": "\u2813",
            "o": "\u2815",
            "r": "\u2817",
            "d": "\u2819",
            "j": "\u281a",
            "g": "\u281b",
            "n": "\u281d",
            "t": "\u281e",
            "q": "\u281f",
            "u": "\u2825",
            "v": "\u2827",
            "x": "\u282d",
            "z": "\u2835",
            "w": "\u283a",
            "y": "\u283d",
            "num": "\u283c",
            "caps": "\u2820",
            ".": "\u2832",
            "'": "\u2804",
            ",": "\u2802",
            "-": "\u2824",
            "/": "\u280c",
            "!": "\u2816",
            "?": "\u2826",
            "$": "\u2832",
            ":": "\u2812",
            ";": "\u2830",
            "(": "\u2836",
            ")": "\u2836",
            "1": "\u2801",
            "2": "\u2803",
            "3": "\u2809",
            "4": "\u2819",
            "5": "\u2811",
            "6": "\u280b",
            "7": "\u281b",
            "8": "\u2813",
            "9": "\u280a",
            "0": "\u281a",
            " ": " ",
        }
        numberPunctuations = [".", ",", "-", "/", "$"]
        escapeCharacters = ["\n", "\r", "\t"]

        output = ""

        for char in text:
            is_number = False
            if char in escapeCharacters:
                output += char
            elif char.isupper():
                if char.lower() in characterUnicodes:
                    output += characterUnicodes["caps"]
                    output += characterUnicodes[char.lower()]
            elif char in characterUnicodes:
                if char.isdigit():
                    if not is_number:
                        is_number = True
                        output += characterUnicodes["num"]
                output += characterUnicodes[char]
            if is_number and char not in numberPunctuations:
                is_number = False

        return output
