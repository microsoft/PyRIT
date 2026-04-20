# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
from typing import Optional

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter
from pyrit.prompt_converter.text_selection_strategy import (
    AllWordsSelectionStrategy,
    WordSelectionStrategy,
)


class WordLevelConverter(PromptConverter):
    """
    Base class for word-level converters. Designed to convert text by processing each word individually.

    This class now uses WordSelectionStrategy to determine which words to convert, providing
    flexible selection options including indices, keywords, proportions, regex patterns, and positions.

    Note:
        The `convert_word_async` method is an abstract method that must be implemented by subclasses.
        It defines the conversion logic for each word.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        word_selection_strategy: Optional[WordSelectionStrategy] = None,
        word_split_separator: Optional[str] = " ",
    ):
        """
        Initialize the converter with the specified selection strategy.

        Args:
            word_selection_strategy (Optional[WordSelectionStrategy]): The strategy for selecting which
                words to convert. If None, all words will be converted. Defaults to None.
            word_split_separator (Optional[str]): Separator used to split words in the input text.
                If None, splits by any whitespace. Defaults to " ".
        """
        super().__init__()
        self._word_selection_strategy = word_selection_strategy or AllWordsSelectionStrategy()
        self._word_split_separator = word_split_separator

    @abc.abstractmethod
    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        pass

    def validate_input(self, prompt: str) -> None:
        """Validate the input before processing (can be overridden by subclasses)."""
        pass

    def join_words(self, words: list[str]) -> str:
        """
        Provide a way for subclasses to override the default behavior of joining words.

        Args:
            words (list[str]): List of words to join.

        Returns:
            str: The joined string.
        """
        return " ".join(words)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into the target format supported by the converter.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted output and its type.

        Raises:
            TypeError: If the prompt is None.
            ValueError: If the input type is not supported.
        """
        if prompt is None:
            raise TypeError("Prompt cannot be None")

        if input_type != "text":
            raise ValueError(f"Input type {input_type} not supported")

        self.validate_input(prompt=prompt)

        if self._word_split_separator is None:
            words = prompt.split()  # if no specified separator, split by all whitespace
        else:
            words = prompt.split(self._word_split_separator)

        selected_indices = self._word_selection_strategy.select_words(words=words)

        # Convert only selected words
        for idx in selected_indices:
            words[idx] = await self.convert_word_async(words[idx])

        return ConverterResult(output_text=self.join_words(words), output_type="text")
