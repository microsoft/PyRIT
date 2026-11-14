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


# Arabic-block letters that do not join the letter after them, taken from the Joining_Type
# column of the Unicode Character Database (ArabicShaping.txt). Right-joining (R) letters such
# as alef, dal, thal, reh, zain, waw and teh marbuta join only the letter before them.
# Non-joining (U) letters (hamza and high hamza) join neither side. Every other letter in the
# block is dual-joining (D).
_RIGHT_JOINING_LETTERS = frozenset(
    chr(code_point)
    for code_point in (
        *range(0x0622, 0x0626),
        0x0627,
        0x0629,
        *range(0x062F, 0x0633),
        0x0648,
        *range(0x0671, 0x0674),
        *range(0x0675, 0x0678),
        *range(0x0688, 0x069A),
        0x06C0,
        *range(0x06C3, 0x06CC),
        0x06CD,
        0x06CF,
        0x06D2,
        0x06D3,
        0x06D5,
        0x06EE,
        0x06EF,
    )
)
_NON_JOINING_LETTERS = frozenset({chr(0x0621), chr(0x0674)})


def _joins_following_letter(char: str) -> bool:
    """
    Determine whether an Arabic letter connects to the letter after it.

    Args:
        char (str): A single character to test.

    Returns:
        bool: True if the character is a dual-joining Arabic letter.
    """
    return _is_arabic_letter(char) and char not in _RIGHT_JOINING_LETTERS and char not in _NON_JOINING_LETTERS


def _joins_preceding_letter(char: str) -> bool:
    """
    Determine whether an Arabic letter connects to the letter before it.

    Args:
        char (str): A single character to test.

    Returns:
        bool: True if the character is a dual-joining or right-joining Arabic letter.
    """
    return _is_arabic_letter(char) and char not in _NON_JOINING_LETTERS


class TatweelConverter(Converter):
    """
    Inserts Arabic tatweel (kashida, U+0640) between adjacent Arabic letters that join.

    The tatweel is a connector that visually elongates a word without changing its meaning. Inserting
    it between letters leaves the text legible to a human reader while changing the underlying code
    point and token sequence. The transformation is deterministic: no language model or randomness is
    involved.

    A tatweel only lengthens an existing connection, so it is inserted only after a letter that joins
    the next letter (not after alef, dal, thal, reh, zain, waw, teh marbuta, hamza or their variants)
    and only before a letter that joins the previous one (not before hamza). Elsewhere it would show
    as a detached stroke inside the word. Characters outside the main Arabic block, and Arabic letters
    not directly followed by another Arabic letter, are left untouched.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, tatweel_count: int = 1) -> None:
        """
        Initialize the converter with the number of tatweel characters to insert.

        Args:
            tatweel_count (int): Number of tatweel characters inserted between adjacent joining Arabic
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
        Convert the given prompt by inserting tatweel between adjacent Arabic letters that join.

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
            if (
                next_index < len(prompt)
                and _joins_following_letter(char)
                and _joins_preceding_letter(prompt[next_index])
            ):
                pieces.append(padding)

        return ConverterResult(output_text="".join(pieces), output_type="text")
