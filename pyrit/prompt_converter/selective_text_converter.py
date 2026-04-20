# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter
from pyrit.prompt_converter.text_selection_strategy import (
    AllWordsSelectionStrategy,
    TextSelectionStrategy,
    TokenSelectionStrategy,
    WordSelectionStrategy,
)
from pyrit.prompt_converter.word_level_converter import WordLevelConverter


class SelectiveTextConverter(PromptConverter):
    """
    A wrapper converter that applies another converter to selected portions of text.

    This converter supports multiple selection strategies:
    - Character-level: Selects a contiguous character range (e.g., IndexSelectionStrategy, RegexSelectionStrategy)
    - Word-level: Selects specific words (e.g., WordIndexSelectionStrategy, WordPositionSelectionStrategy)
    - Token-based: Auto-detects and converts text between ⟪⟫ tokens (TokenSelectionStrategy)

    Most use cases will use word-level strategies for more intuitive selection.

    Example:
        >>> from pyrit.prompt_converter.prompt_converter import Base64Converter, SelectiveTextConverter
        >>> from pyrit.prompt_converter.text_selection_strategy import WordRegexSelectionStrategy
        >>>
        >>> # Convert only words matching a pattern
        >>> strategy = WordRegexSelectionStrategy(pattern=r"\\d+")
        >>> converter = SelectiveTextConverter(
        ...     converter=Base64Converter(),
        ...     selection_strategy=strategy,
        ...     preserve_tokens=True
        ... )
        >>> result = await converter.convert_async(
        ...     prompt="The code is 12345 here"
        ... )
        >>> # Result: "The code is ⟪MTIzNDU=⟫ here"
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        converter: PromptConverter,
        selection_strategy: TextSelectionStrategy,
        preserve_tokens: bool = False,
        start_token: str = "⟪",
        end_token: str = "⟫",
        word_separator: str = " ",
    ) -> None:
        """
        Initialize the selective text converter.

        Args:
            converter (PromptConverter): The converter to apply to the selected text.
            selection_strategy (TextSelectionStrategy): The strategy for selecting which text to convert.
                Can be character-level or word-level strategy.
            preserve_tokens (bool): If True, wraps converted text with start/end tokens.
                This allows subsequent converters in a chain to target different regions. Defaults to False.
            start_token (str): The token to place before converted text when preserve_tokens=True.
                Defaults to "⟪".
            end_token (str): The token to place after converted text when preserve_tokens=True.
                Defaults to "⟫".
            word_separator (str): The separator to use when working with word-level strategies. Defaults to " ".

        Raises:
            ValueError: If the wrapped converter does not support text input/output.
            ValueError: If a word-level selection_strategy is used with a WordLevelConverter
                that has a non-default word_selection_strategy. When SelectiveTextConverter uses
                a WordSelectionStrategy, it passes individual words to the wrapped converter,
                making the wrapped converter's word selection strategy meaningless.
        """
        super().__init__()

        self._validate_converter(converter=converter, selection_strategy=selection_strategy)

        self._converter = converter
        self._selection_strategy = selection_strategy
        self._preserve_tokens = preserve_tokens
        self._start_token = start_token
        self._end_token = end_token
        self._word_separator = word_separator
        self._is_word_level = isinstance(selection_strategy, WordSelectionStrategy)
        self._is_token_based = isinstance(selection_strategy, TokenSelectionStrategy)

    def _validate_converter(
        self,
        *,
        converter: PromptConverter,
        selection_strategy: TextSelectionStrategy,
    ) -> None:
        """
        Validate the converter and selection strategy combination.

        Args:
            converter (PromptConverter): The converter to validate.
            selection_strategy (TextSelectionStrategy): The selection strategy to validate against.

        Raises:
            ValueError: If the converter does not support text input/output.
            ValueError: If a word-level selection strategy is used with a WordLevelConverter
                that has a non-default word_selection_strategy.
        """
        if not converter.input_supported("text"):
            raise ValueError(f"The converter {converter.__class__.__name__} does not support text input")
        if not converter.output_supported("text"):
            raise ValueError(f"The converter {converter.__class__.__name__} does not support text output")

        # Check for conflicting word selection strategies
        is_word_level_selection = isinstance(selection_strategy, WordSelectionStrategy)
        if is_word_level_selection and isinstance(converter, WordLevelConverter):
            has_non_default_strategy = not isinstance(converter._word_selection_strategy, AllWordsSelectionStrategy)
            if has_non_default_strategy:
                raise ValueError(
                    f"Cannot use a WordSelectionStrategy with a {converter.__class__.__name__} that has a "
                    f"non-default word_selection_strategy. When SelectiveTextConverter uses a word-level "
                    f"strategy, it passes individual words to the wrapped converter, making the wrapped "
                    f"converter's word selection strategy meaningless. Either use a character-level "
                    f"selection strategy, or remove the word_selection_strategy from the wrapped converter."
                )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert selected portions of the prompt using the wrapped converter.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data. Must be "text".

        Returns:
            ConverterResult: The result containing the converted output and its type.

        Raises:
            ValueError: If the input type is not "text".
        """
        if input_type != "text":
            raise ValueError(f"SelectiveTextConverter only supports text input, got {input_type}")

        # If using TokenSelectionStrategy, delegate to convert_tokens_async
        if self._is_token_based:
            result = await self._converter.convert_tokens_async(
                prompt=prompt,
                input_type="text",
                start_token=self._start_token,
                end_token=self._end_token,
            )
            # If preserve_tokens is True, the tokens are already in the result
            # If False, convert_tokens_async removes them
            if self._preserve_tokens and self._start_token not in result.output_text:
                # Wrap the result with tokens if they were removed
                result = ConverterResult(
                    output_text=f"{self._start_token}{result.output_text}{self._end_token}", output_type="text"
                )
            return result

        if self._is_word_level:
            return await self._convert_word_level_async(prompt=prompt)
        else:
            return await self._convert_char_level_async(prompt=prompt)

    async def _convert_word_level_async(self, *, prompt: str) -> ConverterResult:
        """
        Convert selected words using word-level selection strategy.

        Args:
            prompt (str): The prompt to be converted.

        Returns:
            ConverterResult: The result containing the converted output and its type.
        """
        words = prompt.split(self._word_separator)

        # Get selected word indices
        selected_indices = self._selection_strategy.select_words(words=words)  # type: ignore

        # If no words selected, return original prompt
        if not selected_indices:
            return ConverterResult(output_text=prompt, output_type="text")

        # Convert selected words
        for idx in selected_indices:
            conversion_result = await self._converter.convert_async(prompt=words[idx], input_type="text")
            converted_word = conversion_result.output_text

            if self._preserve_tokens:
                words[idx] = f"{self._start_token}{converted_word}{self._end_token}"
            else:
                words[idx] = converted_word

        final_text = self._word_separator.join(words)
        return ConverterResult(output_text=final_text, output_type="text")

    async def _convert_char_level_async(self, *, prompt: str) -> ConverterResult:
        """
        Convert a character range using character-level selection strategy.

        Args:
            prompt (str): The prompt to be converted.

        Returns:
            ConverterResult: The result containing the converted output and its type.
        """
        start_idx, end_idx = self._selection_strategy.select_range(text=prompt)

        # If no region selected, return original prompt
        if start_idx == end_idx:
            return ConverterResult(output_text=prompt, output_type="text")

        # Extract the selected region
        before_text = prompt[:start_idx]
        selected_text = prompt[start_idx:end_idx]
        after_text = prompt[end_idx:]

        # Convert the selected region
        conversion_result = await self._converter.convert_async(prompt=selected_text, input_type="text")
        converted_text = conversion_result.output_text

        if self._preserve_tokens:
            converted_text = f"{self._start_token}{converted_text}{self._end_token}"

        final_text = f"{before_text}{converted_text}{after_text}"
        return ConverterResult(output_text=final_text, output_type="text")
