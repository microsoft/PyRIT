# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class UnicodeSubstitutionConverter(PromptConverter):
    """
    Encodes the prompt using any unicode starting point.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, start_value: int = 0xE0000) -> None:
        """
        Initialize the converter with a specified unicode starting point.

        Args:
            start_value (int): The unicode starting point to use for encoding.
        """
        self.startValue = start_value

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by encoding it using any unicode starting point.
        Default is to use invisible flag emoji characters.

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

        ret_text = "".join(chr(self.startValue + ord(ch)) for ch in prompt)
        return ConverterResult(output_text=ret_text, output_type="text")
