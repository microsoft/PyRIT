# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random
from typing import Optional

from pyrit.prompt_converter.text_selection_strategy import WordSelectionStrategy
from pyrit.prompt_converter.word_level_converter import WordLevelConverter


class LeetspeakConverter(WordLevelConverter):
    """
    Converts a string to a leetspeak version.
    """

    def __init__(
        self,
        *,
        deterministic: bool = True,
        custom_substitutions: Optional[dict[str, list[str]]] = None,
        word_selection_strategy: Optional[WordSelectionStrategy] = None,
    ):
        """
        Initialize the converter with optional deterministic mode and custom substitutions.

        Args:
            deterministic (bool): If True, use the first substitution for each character.
                If False, randomly choose a substitution for each character.
            custom_substitutions (Optional[dict]): A dictionary of custom substitutions to override the defaults.
            word_selection_strategy (Optional[WordSelectionStrategy]): Strategy for selecting which words to convert.
                If None, all words will be converted.
        """
        super().__init__(word_selection_strategy=word_selection_strategy)

        default_substitutions = {
            "a": ["4", "@", "/\\", "@", "^", "/-\\"],
            "b": ["8", "6", "13", "|3", "/3", "!3"],
            "c": ["(", "[", "<", "{"],
            "e": ["3"],
            "g": ["9"],
            "i": ["1", "!"],
            "l": ["1", "|"],
            "o": ["0"],
            "s": ["5", "$"],
            "t": ["7"],
            "z": ["2"],
        }

        # Use custom substitutions if provided, otherwise default to the standard ones
        self._leet_substitutions = custom_substitutions if custom_substitutions else default_substitutions
        self._deterministic = deterministic

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        converted_word = []
        for char in word:
            lower_char = char.lower()
            if lower_char in self._leet_substitutions:
                if self._deterministic:
                    # Use the first substitution for deterministic mode
                    converted_word.append(self._leet_substitutions[lower_char][0])
                else:
                    # Randomly select a substitution for each character
                    converted_word.append(random.choice(self._leet_substitutions[lower_char]))
            else:
                # If character not in substitutions, keep it as is
                converted_word.append(char)
        return "".join(converted_word)
