# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class JsonStringConverter(PromptConverter):
    """
    Converts a string to a JSON-safe format using json.dumps().

    This converter is useful when a string needs to be embedded within a JSON payload,
    such as when sending prompts to HTTP targets that expect JSON-formatted requests.
    The converter properly escapes special characters like quotes, newlines, backslashes,
    and unicode characters.

    The output is the escaped string content without the surrounding quotes that
    json.dumps() adds, making it ready to be inserted into a JSON string field.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to a JSON-safe string.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the JSON-escaped string.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        # json.dumps adds surrounding quotes, so we strip them to get just the escaped content
        json_string = json.dumps(prompt)
        escaped_content = json_string[1:-1]

        return ConverterResult(output_text=escaped_content, output_type="text")
