# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.datasets import TextJailBreak
from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class TextJailbreakConverter(PromptConverter):
    """
    Uses a jailbreak template to create a prompt.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, jailbreak_template: TextJailBreak):
        """
        Initialize the converter with the specified jailbreak template.

        Args:
            jailbreak_template (TextJailBreak): The jailbreak template to use for conversion.
        """
        self.jail_break_template = jailbreak_template

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the jailbreak template.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted output and its type.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        jailbreak_prompt = self.jail_break_template.get_jailbreak(prompt=prompt)
        return ConverterResult(output_text=jailbreak_prompt, output_type="text")
