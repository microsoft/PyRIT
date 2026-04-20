# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

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


class DenylistConverter(LLMGenericTextConverter):
    """
    Replaces forbidden words or phrases in a prompt with synonyms using an LLM.

    An existing ``PromptChatTarget`` is used to perform the conversion (like Azure OpenAI).
    """

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptChatTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        system_prompt_template: Optional[SeedPrompt] = None,
        denylist: list[str] = [],
    ):
        """
        Initialize the converter with a target, an optional system prompt template, and a denylist.

        Args:
            converter_target (PromptChatTarget): The target for the prompt conversion.
                Can be omitted if a default has been configured via PyRIT initialization.
            system_prompt_template (Optional[SeedPrompt]): The system prompt template to use for the conversion.
                If not provided, a default template will be used.
            denylist (list[str]): A list of words or phrases that should be replaced in the prompt.
        """
        # set to default strategy if not provided
        system_prompt_template = (
            system_prompt_template
            if system_prompt_template
            else SeedPrompt.from_yaml_file(pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "denylist_converter.yaml")
        )

        super().__init__(
            converter_target=converter_target, system_prompt_template=system_prompt_template, denylist=denylist
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by removing any words or phrases that are in the denylist,
        replacing them with synonymous words.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the modified prompt.
        """
        # check if the prompt contains any words from the  denylist and if so,
        # update the prompt replacing the denied words with synonyms
        denylist = self._prompt_kwargs.get("denylist", [])
        if any(word in prompt for word in denylist):
            return await super().convert_async(prompt=prompt, input_type=input_type)
        logger.info(f"Prompt does not contain any words from the denylist. prompt: {prompt}, denylist: {denylist}")
        return ConverterResult(output_text=prompt, output_type=input_type)
