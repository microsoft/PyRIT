# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.prompt_converter.text_selection_strategy import WordSelectionStrategy
from pyrit.prompt_converter.word_level_converter import WordLevelConverter


class FirstLetterConverter(WordLevelConverter):
    """
    Replaces each word of the prompt with its first letter (or digit).
    Whitespace and words that do not contain any letter or digit are ignored.
    """

    def __init__(
        self,
        *,
        letter_separator: str = " ",
        word_selection_strategy: Optional[WordSelectionStrategy] = None,
    ) -> None:
        """
        Initialize the converter with the specified letter separator and selection strategy.

        Args:
            letter_separator (str): The string used to join the first letters.
            word_selection_strategy (Optional[WordSelectionStrategy]): Strategy for selecting which words to convert.
                If None, all words will be converted.
        """
        super().__init__(word_selection_strategy=word_selection_strategy, word_split_separator=None)
        self.letter_separator = letter_separator

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        stripped_word = "".join(filter(str.isalnum, word))
        return stripped_word[:1]

    def join_words(self, words: list[str]) -> str:
        """
        Join the converted words using the specified letter separator.

        Args:
            words (list[str]): The list of converted words.

        Returns:
            str: The joined string of converted words.
        """
        cleaned_words = list(filter(None, words))
        return self.letter_separator.join(cleaned_words)
