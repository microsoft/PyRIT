# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter.converter import Converter, ConverterResult
from pyrit.converter.text_selection_strategy import (
    AllWordsSelectionStrategy,
    TextSelectionStrategy,
    TokenSelectionStrategy,
    WordSelectionStrategy,
)
from pyrit.converter.word_level_converter import WordLevelConverter
from pyrit.models import ComponentIdentifier, PromptDataType


class SelectiveTextConverter(Converter):
    """
    A wrapper converter that applies another converter to selected portions of text.

    This converter supports multiple selection strategies:
    - Character-level: Selects a contiguous character range (e.g., IndexSelectionStrategy, RegexSelectionStrategy)
    - Word-level: Selects specific words (e.g., WordIndexSelectionStrategy, WordPositionSelectionStrategy)
    - Token-based: Auto-detects and converts text between ⟪⟫ tokens (TokenSelectionStrategy)

    Most use cases will use word-level strategies for more intuitive selection.

    Example:
        >>> from pyrit.converter.converter import Base64Converter, SelectiveTextConverter
        >>> from pyrit.converter.text_selection_strategy import WordRegexSelectionStrategy
        >>>
        >>> # Convert only words matching a pattern
        >>> strategy = WordRegexSelectionStrategy(pattern=r"\\d+")
        >>> converter = SelectiveTextConverter(
        ...     sub_converter=Base64Converter(),
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
        sub_converter: Converter,
        selection_strategy: TextSelectionStrategy,
        preserve_tokens: bool = False,
        start_token: str = "⟪",
        end_token: str = "⟫",
        word_separator: str = " ",
    ) -> None:
        """
        Initialize the selective text converter.

        Args:
            sub_converter (Converter): The converter to apply to the selected text.
            selection_strategy (TextSelectionStrategy): The strategy for selecting which text to convert.
                Can be character-level or word-level strategy.
            preserve_tokens (bool): If True, wraps converted text with start/end tokens.
                With TokenSelectionStrategy, retains each converted region's marker pair, including
                nested pairs. Without markers, wraps the whole converted value. Defaults to False.
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

        self._validate_converter(sub_converter=sub_converter, selection_strategy=selection_strategy)

        self._sub_converter = sub_converter
        self._selection_strategy = selection_strategy
        self._preserve_tokens = preserve_tokens
        self._start_token = start_token
        self._end_token = end_token
        self._word_separator = word_separator
        self._is_word_level = isinstance(selection_strategy, WordSelectionStrategy)
        self._is_token_based = isinstance(selection_strategy, TokenSelectionStrategy)

    def _is_conversion_dispatch(self, prompt: str) -> bool:
        return self._is_token_based and self._has_native_implementation("convert_async")

    def _has_native_implementation(self, method_name: str) -> bool:
        owner = next(cls for cls in type(self).__mro__ if method_name in cls.__dict__)
        return owner is SelectiveTextConverter

    def _get_token_payload_converter(self, *, start_token: str, end_token: str) -> tuple[Converter, bool]:
        converter = self._sub_converter
        preserve_tokens = self._preserve_tokens
        # Collapse only native wrappers, not subclasses that can override conversion.
        while (
            isinstance(converter, SelectiveTextConverter)
            and type(converter) is SelectiveTextConverter
            and converter._is_token_based
            and converter._start_token == start_token
            and converter._end_token == end_token
        ):
            preserve_tokens |= converter._preserve_tokens
            converter = converter._sub_converter
        return converter, preserve_tokens

    async def convert_tokens_async(
        self,
        *,
        prompt: str,
        input_type: PromptDataType = "text",
        start_token: str = "⟪",
        end_token: str = "⟫",
        keep_tokens: bool = False,
    ) -> ConverterResult:
        """
        Apply token selection once, including when called from a pipeline.

        Args:
            prompt (str): The input text.
            input_type (PromptDataType): The input type. Must be text for token selection.
            start_token (str): Opening marker used by the pipeline.
            end_token (str): Closing marker used by the pipeline.
            keep_tokens (bool): Retain the call's marker pair even if preserve_tokens is False.

        Returns:
            ConverterResult: Converted text with selected boundaries consumed or preserved.

        Raises:
            ValueError: If preserving markers around a custom token override is requested.
        """
        if not self._is_token_based or not self._has_native_implementation("convert_async"):
            return await super().convert_tokens_async(
                prompt=prompt,
                input_type=input_type,
                start_token=start_token,
                end_token=end_token,
                keep_tokens=keep_tokens,
            )
        return await self._convert_token_selection_async(
            prompt=prompt,
            input_type=input_type,
            start_token=start_token,
            end_token=end_token,
            keep_tokens=keep_tokens,
        )

    async def _convert_token_selection_async(
        self,
        *,
        prompt: str,
        input_type: PromptDataType,
        start_token: str,
        end_token: str,
        keep_tokens: bool = False,
    ) -> ConverterResult:
        if input_type != "text":
            raise ValueError(f"SelectiveTextConverter only supports text input, got {input_type}")
        if not start_token or not end_token:
            raise ValueError("Start and end tokens must be non-empty.")

        converter, preserve_tokens = self._get_token_payload_converter(start_token=start_token, end_token=end_token)
        if type(converter).convert_tokens_async not in (
            Converter.convert_tokens_async,
            SelectiveTextConverter.convert_tokens_async,
        ):
            if preserve_tokens or keep_tokens:
                raise ValueError(
                    "Cannot preserve selected-region markers around a custom convert_tokens_async override. "
                    "Use preserve_tokens=False and keep_tokens=False to delegate to the override, "
                    "or implement convert_async "
                    "with the shared token parser."
                )
            result = await converter.convert_tokens_async(
                prompt=prompt, input_type=input_type, start_token=start_token, end_token=end_token
            )
            if result.output_type != "text":
                raise ValueError(f"SelectiveTextConverter requires text output, but received {result.output_type}.")
            return result

        async def convert_region_async(text: str) -> ConverterResult:
            result = await converter.convert_async(prompt=text, input_type="text")
            if result.output_type != "text":
                raise ValueError(f"SelectiveTextConverter requires text output, but received {result.output_type}.")
            return result

        return await self._convert_token_regions_async(
            prompt=prompt,
            input_type=input_type,
            start_token=start_token,
            end_token=end_token,
            keep_tokens=preserve_tokens or keep_tokens,
            convert_text_async=convert_region_async,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with selective text converter parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "selection_strategy": self._selection_strategy.__class__.__name__,
                "selection_strategy_params": self._selection_strategy.get_identifier_params(),
                "preserve_tokens": self._preserve_tokens,
                "start_token": self._start_token,
                "end_token": self._end_token,
            },
            sub_converter=self._sub_converter.get_identifier(),
        )

    def _validate_converter(
        self,
        *,
        sub_converter: Converter,
        selection_strategy: TextSelectionStrategy,
    ) -> None:
        """
        Validate the converter and selection strategy combination.

        Args:
            sub_converter (Converter): The converter to validate.
            selection_strategy (TextSelectionStrategy): The selection strategy to validate against.

        Raises:
            ValueError: If the converter does not support text input/output.
            ValueError: If a word-level selection strategy is used with a WordLevelConverter
                that has a non-default word_selection_strategy.
        """
        if not sub_converter.input_supported("text"):
            raise ValueError(f"The converter {sub_converter.__class__.__name__} does not support text input")
        if not sub_converter.output_supported("text"):
            raise ValueError(f"The converter {sub_converter.__class__.__name__} does not support text output")

        # Check for conflicting word selection strategies
        is_word_level_selection = isinstance(selection_strategy, WordSelectionStrategy)
        if is_word_level_selection and isinstance(sub_converter, WordLevelConverter):
            has_non_default_strategy = not isinstance(sub_converter._word_selection_strategy, AllWordsSelectionStrategy)
            if has_non_default_strategy:
                raise ValueError(
                    f"Cannot use a WordSelectionStrategy with a {sub_converter.__class__.__name__} that has a "
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
            ValueError: If token-based conversion produces non-text output.
            ValueError: If marker preservation is requested around a custom token override.
        """
        if input_type != "text":
            raise ValueError(f"SelectiveTextConverter only supports text input, got {input_type}")

        if self._is_token_based:
            if not self._has_native_implementation("convert_tokens_async"):
                return await self.convert_tokens_async(
                    prompt=prompt, input_type=input_type, start_token=self._start_token, end_token=self._end_token
                )
            return await self._convert_token_selection_async(
                prompt=prompt, input_type=input_type, start_token=self._start_token, end_token=self._end_token
            )

        if self._is_word_level:
            return await self._convert_word_level_async(prompt=prompt)
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
        selected_indices = self._selection_strategy.select_words(words=words)  # type: ignore[ty:unresolved-attribute]

        # If no words selected, return original prompt
        if not selected_indices:
            return ConverterResult(output_text=prompt, output_type="text")

        # Convert selected words
        for idx in selected_indices:
            conversion_result = await self._sub_converter.convert_async(prompt=words[idx], input_type="text")
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
        conversion_result = await self._sub_converter.convert_async(prompt=selected_text, input_type="text")
        converted_text = conversion_result.output_text

        if self._preserve_tokens:
            converted_text = f"{self._start_token}{converted_text}{self._end_token}"

        final_text = f"{before_text}{converted_text}{after_text}"
        return ConverterResult(output_text=final_text, output_type="text")
