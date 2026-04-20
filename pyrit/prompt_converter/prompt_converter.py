# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import asyncio
import inspect
import re
from dataclasses import dataclass
from typing import get_args

from pyrit import prompt_converter
from pyrit.identifiers import LegacyIdentifiable
from pyrit.models import PromptDataType


@dataclass
class ConverterResult:
    """The result of a prompt conversion, containing the converted output and its type."""

    #: The converted text output. This is the main result of the conversion.
    output_text: str
    #: The data type of the converted output. Indicates the format/type of the ``output_text``.
    output_type: PromptDataType

    def __str__(self) -> str:
        """
        Representation of the ConverterResult.

        Returns:
            str: A string representation showing the output type and text.
        """
        return f"{self.output_type}: {self.output_text}"


class PromptConverter(LegacyIdentifiable):
    """
    Base class for converters that transform prompts into a different representation or format.

    Concrete subclasses must declare their supported input and output modalities using class attributes:
    - SUPPORTED_INPUT_TYPES: tuple of PromptDataType values that the converter accepts
    - SUPPORTED_OUTPUT_TYPES: tuple of PromptDataType values that the converter produces

    These attributes are enforced at class definition time for all non-abstract subclasses.
    """

    #: Tuple of input modalities supported by this converter. Subclasses must override this.
    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ()
    #: Tuple of output modalities supported by this converter. Subclasses must override this.
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        """
        Validate that concrete subclasses define required class attributes.

        Args:
            **kwargs: Additional keyword arguments passed to the superclass.

        Raises:
            TypeError: If a concrete subclass does not define non-empty SUPPORTED_INPUT_TYPES
                or SUPPORTED_OUTPUT_TYPES.
        """
        super().__init_subclass__(**kwargs)
        # Only validate concrete (non-abstract) classes
        if not inspect.isabstract(cls):
            if not cls.SUPPORTED_INPUT_TYPES:
                raise TypeError(
                    f"{cls.__name__} must define non-empty SUPPORTED_INPUT_TYPES tuple. "
                    f"Declare the input modalities this converter accepts."
                )
            if not cls.SUPPORTED_OUTPUT_TYPES:
                raise TypeError(
                    f"{cls.__name__} must define non-empty SUPPORTED_OUTPUT_TYPES tuple. "
                    f"Declare the output modalities this converter produces."
                )

    def __init__(self) -> None:
        """
        Initialize the prompt converter.
        """
        super().__init__()

    @abc.abstractmethod
    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into the target format supported by the converter.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted output and its type.
        """

    def input_supported(self, input_type: PromptDataType) -> bool:
        """
        Check if the input type is supported by the converter.

        Args:
            input_type (PromptDataType): The input type to check.

        Returns:
            bool: True if the input type is supported, False otherwise.
        """
        return input_type in self.SUPPORTED_INPUT_TYPES

    def output_supported(self, output_type: PromptDataType) -> bool:
        """
        Check if the output type is supported by the converter.

        Args:
            output_type (PromptDataType): The output type to check.

        Returns:
            bool: True if the output type is supported, False otherwise.
        """
        return output_type in self.SUPPORTED_OUTPUT_TYPES

    async def convert_tokens_async(
        self, *, prompt: str, input_type: PromptDataType = "text", start_token: str = "⟪", end_token: str = "⟫"
    ) -> ConverterResult:
        """
        Convert substrings within a prompt that are enclosed by specified start and end tokens. If there are no tokens
        present, the entire prompt is converted.

        Args:
            prompt (str): The input prompt containing text to be converted.
            input_type (str): The type of input data. Defaults to "text".
            start_token (str): The token indicating the start of a substring to be converted. Defaults to "⟪" which is
                relatively distinct.
            end_token (str): The token indicating the end of a substring to be converted. Defaults to "⟫" which is
                relatively distinct.

        Returns:
            str: The prompt with specified substrings converted.

        Raises:
            ValueError: If the input is inconsistent.
        """
        if input_type != "text" and (start_token in prompt or end_token in prompt):
            raise ValueError("Input type must be text when start or end tokens are present.")

        # Find all matches between start_token and end_token
        pattern = re.escape(start_token) + "(.*?)" + re.escape(end_token)
        matches = re.findall(pattern, prompt)

        if not matches:
            # No tokens found, convert the entire prompt
            return await self.convert_async(prompt=prompt, input_type=input_type)

        if prompt.count(start_token) != prompt.count(end_token):
            raise ValueError("Uneven number of start tokens and end tokens.")

        tasks = [self._replace_text_match(match) for match in matches]
        converted_parts = await asyncio.gather(*tasks)

        for original, converted in zip(matches, converted_parts):
            prompt = prompt.replace(f"{start_token}{original}{end_token}", converted.output_text, 1)

        return ConverterResult(output_text=prompt, output_type="text")

    async def _replace_text_match(self, match: str) -> ConverterResult:
        result = await self.convert_async(prompt=match, input_type="text")
        return result

    def get_identifier(self) -> dict[str, str]:
        """
        Return an identifier dictionary for the converter.

        Returns:
            dict: The identifier dictionary.
        """
        public_attributes = {}
        public_attributes["__type__"] = self.__class__.__name__
        public_attributes["__module__"] = self.__class__.__module__
        return public_attributes

    @property
    def supported_input_types(self) -> list[PromptDataType]:
        """
        Returns a list of supported input types for the converter.

        Returns:
            list[PromptDataType]: A list of supported input types.
        """
        return [data_type for data_type in get_args(PromptDataType) if self.input_supported(data_type)]

    @property
    def supported_output_types(self) -> list[PromptDataType]:
        """
        Returns a list of supported output types for the converter.

        Returns:
            list[PromptDataType]: A list of supported output types.
        """
        return [data_type for data_type in get_args(PromptDataType) if self.output_supported(data_type)]


def get_converter_modalities() -> list[tuple[str, list[PromptDataType], list[PromptDataType]]]:
    """
    Retrieve a list of all converter classes and their supported input/output modalities
    by reading the SUPPORTED_INPUT_TYPES and SUPPORTED_OUTPUT_TYPES class attributes.

    Returns:
        list[tuple[str, list[PromptDataType], list[PromptDataType]]]: A sorted list of tuples containing:
            - Converter class name (str)
            - List of supported input modalities (list[PromptDataType])
            - List of supported output modalities (list[PromptDataType])

        Sorted by input modality, then output modality, then converter name.
    """
    converter_modalities = []

    # Get all converter classes from the __all__ list
    for name in prompt_converter.__all__:
        if name in ("ConverterResult", "PromptConverter") or "Strategy" in name:
            continue

        converter_class = getattr(prompt_converter, name)

        # Skip if not a class or not a subclass of PromptConverter
        if not isinstance(converter_class, type) or not issubclass(converter_class, PromptConverter):
            continue

        # Read the class attributes
        input_modalities = list(converter_class.SUPPORTED_INPUT_TYPES)
        output_modalities = list(converter_class.SUPPORTED_OUTPUT_TYPES)

        converter_modalities.append((name, input_modalities, output_modalities))

    # Sort by input modality, then output modality, then converter name
    converter_modalities.sort(key=lambda x: (x[1][0] if x[1] else "", x[2][0] if x[2] else "", x[0]))

    return converter_modalities
