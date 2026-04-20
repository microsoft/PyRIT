# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class NatoConverter(PromptConverter):
    """
    Converts text into NATO phonetic alphabet representation.

    This converter transforms standard text into NATO phonetic alphabet format,
    where each letter is replaced with its corresponding NATO phonetic code word
    (e.g., "A" becomes "Alfa", "B" becomes "Bravo"). Only alphabetic characters
    are converted; non-alphabetic characters are ignored.

    The NATO phonetic alphabet is the most widely used spelling alphabet, designed
    to improve clarity of voice communication. This converter can be used to test
    how AI systems handle phonetically encoded text, which can be used to obfuscate
    potentially harmful content.

    Reference: https://en.wikipedia.org/wiki/NATO_phonetic_alphabet

    Example:
        Input: "Hello"
        Output: "Hotel Echo Lima Lima Oscar"
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    _NATO_MAP = {
        "A": "Alfa",
        "B": "Bravo",
        "C": "Charlie",
        "D": "Delta",
        "E": "Echo",
        "F": "Foxtrot",
        "G": "Golf",
        "H": "Hotel",
        "I": "India",
        "J": "Juliett",
        "K": "Kilo",
        "L": "Lima",
        "M": "Mike",
        "N": "November",
        "O": "Oscar",
        "P": "Papa",
        "Q": "Quebec",
        "R": "Romeo",
        "S": "Sierra",
        "T": "Tango",
        "U": "Uniform",
        "V": "Victor",
        "W": "Whiskey",
        "X": "Xray",
        "Y": "Yankee",
        "Z": "Zulu",
    }

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given text into NATO phonetic alphabet representation.

        Args:
            prompt (str): The text to be converted to NATO phonetic alphabet.
            input_type (PromptDataType, optional): Type of input data. Defaults to "text".

        Returns:
            ConverterResult: The text converted to NATO phonetic alphabet format.

        Raises:
            ValueError: If the input type is not supported (only "text" is supported).
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        nato_text = self._convert_to_nato(prompt)

        return ConverterResult(output_text=nato_text, output_type="text")

    def _convert_to_nato(self, text: str) -> str:
        """
        Convert text to NATO phonetic alphabet representation.

        Args:
            text (str): The text to convert.

        Returns:
            str: The NATO phonetic alphabet representation, with code words separated by spaces.
        """
        output = []
        for char in text.upper():
            if char in self._NATO_MAP:
                output.append(self._NATO_MAP[char])

        return " ".join(output)
