# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import logging

import ecoji

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class EcojiConverter(PromptConverter):
    """
    Converter that encodes text using Ecoji encoding.

    Ecoji is an encoding scheme that represents binary data using emojis.
    See https://ecoji.io/ for more details.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self) -> None:
        """Initialize the Ecoji converter."""

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to Ecoji encoding.

        Args:
            prompt (str): The text to encode.
            input_type (PromptDataType): The type of input. Defaults to "text".

        Returns:
            ConverterResult: The result containing the Ecoji-encoded text.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type=input_type):
            raise ValueError("Input type not supported")

        encoded_text = self._encode_to_ecoji(prompt)
        return ConverterResult(output_text=encoded_text, output_type="text")

    def _encode_to_ecoji(self, text: str) -> str:
        """
        Encode text to Ecoji format.

        Args:
            text (str): The text to encode.

        Returns:
            str: The Ecoji-encoded text.
        """
        text_bytes = text.encode("utf-8")
        reader = io.BytesIO(text_bytes)
        writer = io.StringIO()

        ecoji.encode(reader, writer)

        return writer.getvalue()
