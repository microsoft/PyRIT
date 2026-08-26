# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType


class BrailleConverter(Converter):
    """
    Converts text into Braille Unicode representation.

    This converter transforms standard text into Braille patterns using Unicode
    Braille characters (U+2800 to U+28FF). Every printable ASCII character is
    mapped: letters, digits, punctuation, and symbols. Uppercase letters are
    prefixed with the Braille capitalization indicator and digit runs with the
    number indicator. Characters with no Braille mapping (accented and CJK
    letters, emoji, control characters) pass through unchanged rather than being
    dropped, so the encoded prompt keeps its meaning.

    The letter, digit, and core punctuation mappings are based on the
    implementation from Garak:
    https://github.com/NVIDIA/garak/blob/main/garak/probes/encoding.py
    The ASCII symbol cells follow Unified English Braille (UEB).

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
        character_unicodes = {
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
            ";": "\u2806",
            "(": "\u2836",
            ")": "\u2836",
            # Remaining printable ASCII, as two-cell UEB symbol sequences.
            "@": "\u2808\u2801",
            "#": "\u2838\u2839",
            "%": "\u2828\u2834",
            "&": "\u2808\u282f",
            "*": "\u2810\u2814",
            "+": "\u2810\u2816",
            "<": "\u2808\u2823",
            "=": "\u2810\u2836",
            ">": "\u2808\u281c",
            '"': "\u2820\u2836",
            "[": "\u2828\u2823",
            "]": "\u2828\u281c",
            "\\": "\u2838\u2821",
            "^": "\u2808\u2822",
            "_": "\u2828\u2824",
            "`": "\u2828\u2821",
            "{": "\u2838\u2823",
            "}": "\u2838\u281c",
            "|": "\u2838\u2833",
            "~": "\u2808\u2814",
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
        number_punctuations = [".", ",", "-", "/", "$"]

        output = ""

        is_number = False
        for char in text:
            if char in character_unicodes:
                if char.isdigit() and not is_number:
                    is_number = True
                    output += character_unicodes["num"]
                output += character_unicodes[char]
            elif char.isupper() and char.lower() in character_unicodes:
                output += character_unicodes["caps"]
                output += character_unicodes[char.lower()]
            else:
                output += char
            if is_number and not char.isdigit() and char not in number_punctuations:
                is_number = False

        return output
