# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import urllib.parse

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class UrlConverter(Converter):
    """
    Converts a prompt to a URL-encoded string.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, safe: str = "/") -> None:
        """
        Initialize the converter with safe characters that should not be converted.

        Args:
            safe (str): Characters that should not be URL-encoded. Defaults to "/",
                matching urllib.parse.quote. Set to "" to encode forward slashes.
        """
        super().__init__()
        self._safe = safe

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with encoding parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(params={"safe": self._safe})

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into a URL-encoded string.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the URL-encoded prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        return ConverterResult(output_text=urllib.parse.quote(prompt, safe=self._safe), output_type="text")
