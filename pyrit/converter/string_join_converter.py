# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter.text_selection_strategy import AllWordsSelectionStrategy, WordSelectionStrategy
from pyrit.converter.word_level_converter import WordLevelConverter
from pyrit.models import ComponentIdentifier


class StringJoinConverter(WordLevelConverter):
    """
    Converts text by joining its characters with the specified join value.
    """

    def __init__(
        self,
        *,
        join_value: str = "-",
        word_selection_strategy: WordSelectionStrategy | None = None,
    ) -> None:
        """
        Initialize the converter with the specified join value and selection strategy.

        Args:
            join_value (str): The string used to join characters of each word.
            word_selection_strategy (WordSelectionStrategy | None): Strategy for selecting which words to convert.
                If None, all words will be converted.
        """
        super().__init__(word_selection_strategy=word_selection_strategy)
        self._join_value = join_value

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with join and word-selection parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        base_identifier = super()._build_identifier()

        # Preserve the legacy identifier for the default all-words configuration.
        # Non-default selection strategies must include their parameters because
        # they can change the converter's output.
        if type(self._word_selection_strategy) is AllWordsSelectionStrategy:
            return self._create_identifier(
                params={"join_value": self._join_value},
            )

        return self._create_identifier(
            params={**base_identifier.params, "join_value": self._join_value},
        )

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        return self._join_value.join(word)
