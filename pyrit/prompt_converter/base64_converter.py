# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import binascii
from typing import Literal

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class Base64Converter(PromptConverter):
    """
    Converter that encodes text to base64 format.

    This converter takes input text and converts it to base64 encoding,
    which can be useful for obfuscating text or testing how systems
    handle encoded content.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    EncodingFunc = Literal[
        "b64encode",
        "urlsafe_b64encode",
        "standard_b64encode",
        "b2a_base64",
        "b16encode",
        "b32encode",
        "a85encode",
        "b85encode",
    ]

    def __init__(self, *, encoding_func: EncodingFunc = "b64encode") -> None:
        """
        Initialize the Base64Converter.

        Args:
            encoding_func: The base64 encoding function to use. Defaults to "b64encode".
        """
        self._encoding_func = encoding_func

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to base64 encoding.

        Args:
            prompt: The prompt to be converted.
            input_type: Type of data, unused for this converter.

        Returns:
            The converted text representation of the original prompt in base64.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        string_bytes = prompt.encode("utf-8")
        if self._encoding_func == "b64encode":
            encoded_bytes = base64.b64encode(string_bytes)
        elif self._encoding_func == "urlsafe_b64encode":
            encoded_bytes = base64.urlsafe_b64encode(string_bytes)
        elif self._encoding_func == "standard_b64encode":
            encoded_bytes = base64.standard_b64encode(string_bytes)
        elif self._encoding_func == "b2a_base64":
            encoded_bytes = binascii.b2a_base64(string_bytes)
        elif self._encoding_func == "b16encode":
            encoded_bytes = base64.b16encode(string_bytes)
        elif self._encoding_func == "b32encode":
            encoded_bytes = base64.b32encode(string_bytes)
        elif self._encoding_func == "a85encode":
            encoded_bytes = base64.a85encode(string_bytes)
        elif self._encoding_func == "b85encode":
            encoded_bytes = base64.b85encode(string_bytes)
        else:
            raise ValueError("Unsupported encoding function")

        return ConverterResult(output_text=encoded_bytes.decode("utf-8"), output_type="text")
