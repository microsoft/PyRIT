# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random
import string
from typing import Optional

from pyrit.prompt_converter.text_selection_strategy import (
    WordProportionSelectionStrategy,
    WordSelectionStrategy,
)
from pyrit.prompt_converter.word_level_converter import WordLevelConverter


class CharSwapConverter(WordLevelConverter):
    """
    Applies character swapping to words in the prompt to test adversarial textual robustness.
    """

    def __init__(
        self,
        *,
        max_iterations: int = 10,
        word_selection_strategy: Optional[WordSelectionStrategy] = None,
    ):
        """
        Initialize the converter with the specified parameters.

        By default, 20% of randomly selected words will be perturbed.

        Args:
            max_iterations (int): Number of times to generate perturbed prompts.
                The higher the number the higher the chance that words are different from the original prompt.
            word_selection_strategy (Optional[WordSelectionStrategy]): Strategy for selecting which words to convert.
                If None, defaults to WordProportionSelectionStrategy(proportion=0.2).

        Raises:
            ValueError: If max_iterations is not greater than 0.
        """
        # Default to 20% proportion if no strategy provided
        if word_selection_strategy is None:
            word_selection_strategy = WordProportionSelectionStrategy(proportion=0.2)

        super().__init__(word_selection_strategy=word_selection_strategy)

        # Ensure max_iterations is positive
        if max_iterations <= 0:
            raise ValueError("max_iterations must be greater than 0")

        self.max_iterations = max_iterations

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        return self._perturb_word(word)

    def _perturb_word(self, word: str) -> str:
        """
        Perturbs a word by swapping two adjacent characters.

        Args:
            word (str): The word to perturb.

        Returns:
            str: The perturbed word with swapped characters.
        """
        if word not in string.punctuation and len(word) > 3:
            idx_elements = list(word)
            for _ in range(self.max_iterations):
                idx1 = random.randint(1, len(word) - 2)
                # Swap characters
                idx_elements[idx1], idx_elements[idx1 + 1] = (
                    idx_elements[idx1 + 1],
                    idx_elements[idx1],
                )
            return "".join(idx_elements)
        return word
