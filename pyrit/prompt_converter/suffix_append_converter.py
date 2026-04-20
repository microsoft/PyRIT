# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class SuffixAppendConverter(PromptConverter):
    """
    Appends a specified suffix to the prompt.
    E.g. with a suffix `!!!`, it converts a prompt of `test` to `test !!!`.

    See https://github.com/microsoft/PyRIT/tree/main/pyrit/auxiliary_attacks/gcg for adversarial suffix generation.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, suffix: str):
        """
        Initialize the converter with the specified suffix.

        Args:
            suffix (str): The suffix to append to the prompt.

        Raises:
            ValueError: If ``suffix`` is not provided.
        """
        if not suffix:
            raise ValueError("Please specify a suffix (str) to be appended to the prompt.")

        self._suffix = suffix

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with suffix parameter.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "suffix": self._suffix,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by appending the specified suffix.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType, optional): Type of input data. Defaults to "text".

        Returns:
            ConverterResult: The prompt with the suffix appended.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        return ConverterResult(output_text=prompt + " " + self._suffix, output_type="text")
