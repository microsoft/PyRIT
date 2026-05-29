# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class FlipConverter(PromptConverter):
    """
    Flips the input text prompt. For example, "hello me" would be converted to "em olleh".
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by reversing the text.

        Args:
            prompt: The prompt to be converted.
            input_type: Type of data.

        Returns:
            The converted text representation of the original prompt with characters reversed.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        rev_prompt = prompt[::-1]

        return ConverterResult(output_text=rev_prompt, output_type="text")
