# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import unicodedata

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

logger = logging.getLogger(__name__)

# Arabic tatweel / kashida (U+0640)
_TATWEEL = chr(0x0640)

# Bounds of the main Arabic Unicode block (U+0600-U+06FF)
_ARABIC_BLOCK_START = 0x0600
_ARABIC_BLOCK_END = 0x06FF


def _is_arabic_letter(char: str) -> bool:
    """
    Determine whether a character is a letter in the main Arabic Unicode block.

    Args:
        char (str): A single character to test.

    Returns:
        bool: True if the character is an Arabic letter (category ``Lo`` within U+0600-U+06FF).
    """
    return unicodedata.category(char) == "Lo" and _ARABIC_BLOCK_START <= ord(char) <= _ARABIC_BLOCK_END


class TatweelConverter(Converter):
    """
    Inserts Arabic tatweel (kashida, U+0640) between adjacent Arabic letters.

    The tatweel is a connector that visually elongates a word without changing its meaning. Inserting
    it between letters leaves the text legible to a human reader while changing the underlying code
    point and token sequence. The transformation is deterministic: no language model or randomness is
    involved. Characters outside the main Arabic block, and Arabic letters not directly followed by
    another Arabic letter, are left untouched.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, tatweel_count: int = 1) -> None:
        """
        Initialize the converter with the number of tatweel characters to insert.

        Args:
            tatweel_count (int): Number of tatweel characters inserted between adjacent Arabic
                letters. Must be at least 1. Defaults to 1.

        Raises:
            ValueError: If ``tatweel_count`` is less than 1.
        """
        super().__init__()

        if tatweel_count < 1:
            raise ValueError(f"tatweel_count must be at least 1, got {tatweel_count}.")

        self._tatweel_count = tatweel_count

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the tatweel count parameter.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(params={"tatweel_count": self._tatweel_count})

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by inserting tatweel between adjacent Arabic letters.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the elongated text.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        padding = _TATWEEL * self._tatweel_count
        pieces: list[str] = []
        for index, char in enumerate(prompt):
            pieces.append(char)
            next_index = index + 1
            if next_index < len(prompt) and _is_arabic_letter(char) and _is_arabic_letter(prompt[next_index]):
                pieces.append(padding)

        return ConverterResult(output_text="".join(pieces), output_type="text")
