# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""
Toxic Sentence Generator Converter module for generating potentially harmful content
to test AI safety mechanisms.
"""

import logging
import pathlib
from typing import Optional

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.models import PromptDataType, SeedPrompt
from pyrit.prompt_converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.prompt_converter.prompt_converter import ConverterResult
from pyrit.prompt_target import PromptChatTarget

logger = logging.getLogger(__name__)


class ToxicSentenceGeneratorConverter(LLMGenericTextConverter):
    """
    Generates toxic sentence starters using an LLM.

    An existing ``PromptChatTarget`` is used to perform the conversion (like Azure OpenAI).

    Based on Project Moonshot's attack module that generates toxic sentences to test LLM
    safety guardrails:
    https://github.com/aiverify-foundation/moonshot-data/blob/main/attack-modules/toxic_sentence_generator.py
    """

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptChatTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        prompt_template: Optional[SeedPrompt] = None,
    ):
        """
        Initialize the converter with a specific target and template.

        Args:
            converter_target (PromptChatTarget): The endpoint that converts the prompt.
                Can be omitted if a default has been configured via PyRIT initialization.
            prompt_template (SeedPrompt): The seed prompt template to use. If not provided,
                                          defaults to the ``toxic_sentence_generator.yaml``.
        """
        # set to default strategy if not provided
        prompt_template = (
            prompt_template
            if prompt_template
            else SeedPrompt.from_yaml_file(pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "toxic_sentence_generator.yaml")
        )

        super().__init__(converter_target=converter_target, system_prompt_template=prompt_template)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into a toxic sentence starter.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The conversion result containing the toxic sentence starter.
        """
        # Add the prompt to _prompt_kwargs before calling the base method
        self._prompt_kwargs["prompt"] = prompt
        return await super().convert_async(prompt=prompt, input_type=input_type)

    def input_supported(self, input_type: PromptDataType) -> bool:
        """
        Check if the input type is supported.

        Args:
            input_type (PromptDataType): The type of input data.

        Returns:
            bool: True if the input type is supported, False otherwise.
        """
        return input_type == "text"

    def output_supported(self, output_type: PromptDataType) -> bool:
        """
        Check if the output type is supported.

        Args:
            output_type (PromptDataType): The type of output data.

        Returns:
            bool: True if the output type is supported, False otherwise.
        """
        return output_type == "text"
