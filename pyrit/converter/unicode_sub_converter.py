# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class UnicodeSubstitutionConverter(Converter):
    """
    Encodes the prompt using any unicode starting point.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, start_value: int = 0xE0000) -> None:
        """
        Initialize the converter with a specified unicode starting point.

        Args:
            start_value (int): The unicode starting point to use for encoding.

        Raises:
            ValueError: If ``start_value`` is outside the Unicode code point range.
        """
        if not 0 <= start_value <= 0x10FFFF:
            raise ValueError("start_value must be a valid Unicode code point between 0 and 0x10FFFF")
        self.startValue = start_value

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with unicode substitution parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "start_value": self.startValue,
            }
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by encoding it using any unicode starting point.
        Default is to use invisible flag emoji characters.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted output and its type.

        Raises:
            ValueError: If the input type is not supported, or if shifting a character by ``start_value``
                does not give a valid Unicode scalar value (above U+10FFFF or a surrogate).
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        converted = []
        for ch in prompt:
            code_point = self.startValue + ord(ch)
            if code_point > 0x10FFFF or 0xD800 <= code_point <= 0xDFFF:
                raise ValueError(
                    f"start_value {self.startValue:#x} cannot encode {ch!r}: the result {code_point:#x} "
                    "is not a valid Unicode scalar value."
                )
            converted.append(chr(code_point))

        return ConverterResult(output_text="".join(converted), output_type="text")
