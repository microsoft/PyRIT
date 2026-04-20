# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

import base2048

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class Base2048Converter(PromptConverter):
    """
    Converter that encodes text to base2048 format.

    This converter takes input text and converts it to base2048 encoding,
    which uses 2048 different Unicode characters to represent binary data.
    This can be useful for obfuscating text or testing how systems
    handle encoded Unicode content.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self) -> None:
        """Initialize the Base2048Converter."""
        pass

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to base2048 encoding.

        Args:
            prompt: The prompt to be converted.
            input_type: Type of data, unused for this converter.

        Returns:
            The converted text representation of the original prompt in base2048.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        string_bytes = prompt.encode("utf-8")
        encoded_bytes = base2048.encode(string_bytes)

        return ConverterResult(output_text=encoded_bytes, output_type="text")
