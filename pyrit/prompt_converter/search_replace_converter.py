# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random
import re

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class SearchReplaceConverter(PromptConverter):
    """
    Converts a string by replacing chosen phrase with a new phrase of choice.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, pattern: str, replace: str | list[str], regex_flags: int = 0) -> None:
        """
        Initialize the converter with the specified regex pattern and replacement phrase(s).

        Args:
            pattern (str): The regex pattern to replace.
            replace (str | list[str]): The new phrase to replace with. Can be a single string or a list of strings.
                If a list is provided, a random element will be chosen for replacement.
            regex_flags (int): Regex flags to use for the replacement. Defaults to 0 (no flags).
        """
        self.pattern = pattern
        self.replace_list = [replace] if isinstance(replace, str) else replace

        self.regex_flags = regex_flags

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by replacing the specified pattern with a random choice from the replacement list.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted text.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        replace = random.choice(self.replace_list)

        return ConverterResult(
            output_text=re.sub(self.pattern, replace, prompt, flags=self.regex_flags), output_type="text"
        )
