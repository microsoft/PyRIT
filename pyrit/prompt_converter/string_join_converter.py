# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.prompt_converter.text_selection_strategy import WordSelectionStrategy
from pyrit.prompt_converter.word_level_converter import WordLevelConverter


class StringJoinConverter(WordLevelConverter):
    """
    Converts text by joining its characters with the specified join value.
    """

    def __init__(
        self,
        *,
        join_value: str = "-",
        word_selection_strategy: Optional[WordSelectionStrategy] = None,
    ) -> None:
        """
        Initialize the converter with the specified join value and selection strategy.

        Args:
            join_value (str): The string used to join characters of each word.
            word_selection_strategy (Optional[WordSelectionStrategy]): Strategy for selecting which words to convert.
                If None, all words will be converted.
        """
        super().__init__(word_selection_strategy=word_selection_strategy)
        self.join_value = join_value

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        return self.join_value.join(word)
