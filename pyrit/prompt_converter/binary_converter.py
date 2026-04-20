# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from enum import Enum
from typing import Optional

from pyrit.prompt_converter.text_selection_strategy import WordSelectionStrategy
from pyrit.prompt_converter.word_level_converter import WordLevelConverter


class BinaryConverter(WordLevelConverter):
    """
    Transforms input text into its binary representation with configurable bits per character (8, 16, or 32).
    """

    class BitsPerChar(Enum):
        """The number of bits per character for binary conversion."""

        BITS_8 = 8  #: 8 bits per character, suitable for ASCII characters.
        BITS_16 = 16  #: 16 bits per character, suitable for Unicode characters.
        BITS_32 = 32  #: 32 bits per character, suitable for extended Unicode characters.

    def __init__(
        self,
        *,
        bits_per_char: BinaryConverter.BitsPerChar = BitsPerChar.BITS_16,
        word_selection_strategy: Optional[WordSelectionStrategy] = None,
    ):
        """
        Initialize the converter with the specified bits per character and selection strategy.

        Args:
            bits_per_char (BinaryConverter.BitsPerChar): Number of bits to use for each character (8, 16, or 32).
                Default is 16 bits.
            word_selection_strategy (Optional[WordSelectionStrategy]): Strategy for selecting which words to convert.
                If None, all words will be converted.

        Raises:
            TypeError: If ``bits_per_char`` is not an instance of BinaryConverter.BitsPerChar Enum.
        """
        super().__init__(word_selection_strategy=word_selection_strategy)

        if not isinstance(bits_per_char, BinaryConverter.BitsPerChar):
            raise TypeError("bits_per_char must be an instance of BinaryConverter.BitsPerChar Enum.")
        self.bits_per_char = bits_per_char

    def validate_input(self, prompt: str) -> None:
        """
        Check if ``bits_per_char`` is sufficient for the characters in the prompt.

        Args:
            prompt (str): The input text prompt to validate.

        Raises:
            ValueError: If ``bits_per_char`` is too small to represent any character in the prompt.
        """
        bits = self.bits_per_char.value
        max_code_point = max((ord(char) for char in prompt), default=0)
        min_bits_required = max_code_point.bit_length()
        if bits < min_bits_required:
            raise ValueError(
                f"bits_per_char={bits} is too small for the characters in the prompt. "
                f"Minimum required bits: {min_bits_required}."
            )

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        bits = self.bits_per_char.value
        return " ".join(format(ord(char), f"0{bits}b") for char in word)

    def join_words(self, words: list[str]) -> str:
        """
        Join the converted words with the binary representation of a space.

        Args:
            words (list[str]): The list of converted words.

        Returns:
            str: The final joined string with spaces in binary format.
        """
        space_binary = format(ord(" "), f"0{self.bits_per_char.value}b")
        return f" {space_binary} ".join(words)
