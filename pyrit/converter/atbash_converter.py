# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

from pyrit.converter._cipher_description import CIPHER_EXAMPLE, maybe_render_description
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class AtbashConverter(Converter):
    """
    Encodes text using the Atbash cipher.

    Uses the following to encode:
        - ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789
        - ZYXWVUTSRQPONMLKJIHGFEDCBA 9876543210

    'Hello 123' would encode to 'Svool 876'.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, append_description: bool = False) -> None:
        """
        Initialize the converter with an option to append a description.

        Args:
            append_description (bool): If True, appends plaintext "expert" text to the prompt.
                This includes instructions to only communicate using the cipher,
                a description of the cipher, and an example encoded using the cipher.
        """
        self.append_description = append_description

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Atbash cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "append_description": self.append_description,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the Atbash cipher.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the encoded prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        output_text = maybe_render_description(
            self._atbash(prompt),
            append_description=self.append_description,
            yaml_stem="atbash",
            example_encoded=self._atbash(CIPHER_EXAMPLE),
        )
        return ConverterResult(output_text=output_text, output_type="text")

    def _atbash(self, text: str) -> str:
        def reverse(alphabet: str) -> str:
            return alphabet[::-1]

        alphabet = (string.ascii_lowercase, string.ascii_uppercase, string.digits)
        reversed_alphabet = tuple(map(reverse, alphabet))
        translation_table = str.maketrans("".join(alphabet), "".join(reversed_alphabet))
        return text.translate(translation_table)
