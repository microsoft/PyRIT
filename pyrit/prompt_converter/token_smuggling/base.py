# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import logging
from typing import Literal, Tuple

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class SmugglerConverter(PromptConverter, abc.ABC):
    """
    Abstract base class for token smuggling converters.

    Provides the common asynchronous conversion interface and enforces
    implementation of encode_message and decode_message in subclasses.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, action: Literal["encode", "decode"] = "encode") -> None:
        """
        Initialize the converter with options for encoding/decoding.

        Args:
            action (Literal["encode", "decode"]): The action to perform.

        Raises:
            ValueError: If the action is not 'encode' or 'decode'.
        """
        if action not in ["encode", "decode"]:
            raise ValueError("Action must be either 'encode' or 'decode'")
        self.action = action

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by either encoding or decoding it based on the specified action.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted text and its type.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")
        if self.action == "encode":
            summary, encoded = self.encode_message(message=prompt)
            logger.info(f"Encoded message summary: {summary}")
            return ConverterResult(output_text=encoded, output_type="text")
        else:
            decoded = self.decode_message(message=prompt)
            return ConverterResult(output_text=decoded, output_type="text")

    def input_supported(self, input_type: PromptDataType) -> bool:
        """
        Check if the input type is supported by the converter.

        Args:
            input_type (PromptDataType): The input type to check.

        Returns:
            bool: True if the input type is supported, False otherwise.
        """
        return input_type == "text"

    def output_supported(self, output_type: PromptDataType) -> bool:
        """
        Check if the output type is supported.

        Args:
            output_type (PromptDataType): The type of output data.

        Returns:
            bool: True if the output type is supported, False otherwise.
        """
        return output_type == "text"

    @abc.abstractmethod
    def encode_message(self, *, message: str) -> Tuple[str, str]:
        """
        Encode the given message.

        Must be implemented by subclasses.

        Args:
            message (str): The message to encode.

        Returns:
            Tuple[str, str]: A tuple containing a summary and the encoded message.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def decode_message(self, *, message: str) -> str:
        """
        Decode the given message.

        Must be implemented by subclasses.

        Args:
            message (str): The encoded message.

        Returns:
            str: The decoded message.
        """
        raise NotImplementedError
