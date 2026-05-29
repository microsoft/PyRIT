# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class ZeroWidthConverter(PromptConverter):
    """
    Injects zero-width spaces between characters in the provided text to bypass content safety mechanisms.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    #: Constant for zero-width space character.
    ZERO_WIDTH_SPACE = "\u200b"

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by injecting zero-width spaces between each character.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the modified prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Only 'text' input type is supported.")

        # Insert zero-width spaces between each character
        modified_text = self.ZERO_WIDTH_SPACE.join(prompt)

        return ConverterResult(output_text=modified_text, output_type="text")
