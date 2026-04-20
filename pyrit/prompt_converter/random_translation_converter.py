# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import random
from pathlib import Path
from typing import List, Optional

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH, DATASETS_PATH
from pyrit.models import PromptDataType, SeedDataset, SeedPrompt
from pyrit.prompt_converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.prompt_converter.prompt_converter import ConverterResult
from pyrit.prompt_converter.text_selection_strategy import WordSelectionStrategy
from pyrit.prompt_converter.word_level_converter import WordLevelConverter
from pyrit.prompt_target import PromptChatTarget

logger = logging.getLogger(__name__)


class RandomTranslationConverter(LLMGenericTextConverter, WordLevelConverter):
    """
    Translates each individual word in a prompt to a random language using an LLM.

    An existing ``PromptChatTarget`` is used to perform the translation (like Azure OpenAI).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    # Default language list
    _DEFAULT_LANGUAGES_SEED_PROMPT_PATH = Path(DATASETS_PATH) / "lexicons" / "languages_most_spoken.yaml"

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptChatTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        system_prompt_template: Optional[SeedPrompt] = None,
        languages: Optional[List[str]] = None,
        word_selection_strategy: Optional[WordSelectionStrategy] = None,
    ):
        """
        Initialize the converter with a target, an optional system prompt template, and language options.

        Args:
            converter_target (PromptChatTarget): The target for the prompt conversion.
                Can be omitted if a default has been configured via PyRIT initialization.
            system_prompt_template (Optional[SeedPrompt]): The system prompt template to use for the conversion.
                If not provided, a default template will be used.
            languages (Optional[List[str]]): The list of available languages to use for translation.
            word_selection_strategy (Optional[WordSelectionStrategy]): Strategy for selecting which words to convert.
                If None, all words will be converted.

        Raises:
            ValueError: If converter_target is not provided and no default has been configured.
        """
        if converter_target is None:
            raise ValueError(
                "converter_target is required for LLM-based converters. "
                "Either pass it explicitly or configure a default via PyRIT initialization "
                "(e.g., initialize_pyrit_async with SimpleInitializer or AIRTInitializer)."
            )
        # set to default strategy if not provided
        system_prompt_template = (
            system_prompt_template
            if system_prompt_template
            else SeedPrompt.from_yaml_file(Path(CONVERTER_SEED_PROMPT_PATH) / "random_translation_converter.yaml")
        )

        LLMGenericTextConverter.__init__(
            self,
            converter_target=converter_target,
            system_prompt_template=system_prompt_template,
        )

        WordLevelConverter.__init__(self, word_selection_strategy=word_selection_strategy, word_split_separator=None)

        if not languages:
            default_languages = SeedDataset.from_yaml_file(
                RandomTranslationConverter._DEFAULT_LANGUAGES_SEED_PROMPT_PATH
            )
            self.languages = [prompt.value for prompt in default_languages.prompts]
        else:
            self.languages = languages

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into the target format supported by the converter.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted output and its type.
        """
        words = prompt.split()
        selected_indices = self._word_selection_strategy.select_words(words=words)

        language_word_pairs = [f"NOOP: {word}" for word in words]

        # Translate only selected words
        for idx in selected_indices:
            language_word_pairs[idx] = await self.convert_word_async(words[idx])

        llm_prompt = ""
        for pair in language_word_pairs:
            llm_prompt += f"{pair}\n"

        return await LLMGenericTextConverter.convert_async(self, prompt=llm_prompt, input_type="text")

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        language = random.choice(self.languages)
        return f"{language}: {word}"
