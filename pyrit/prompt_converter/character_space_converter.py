# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class CharacterSpaceConverter(PromptConverter):
    """
    Spaces out the input prompt and removes specified punctuations.

    For more information on the bypass strategy, refer to:
    https://www.robustintelligence.com/blog-posts/bypassing-metas-llama-classifier-a-simple-jailbreak
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by removing punctuation and spacing out characters.

        Args:
            prompt (str): The input text prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted text.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")
        converted_text = re.sub("[!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~]", "", " ".join(prompt))
        return ConverterResult(output_text=converted_text, output_type="text")
