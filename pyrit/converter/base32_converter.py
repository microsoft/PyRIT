# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
from typing import Literal

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class Base32Converter(Converter):
    """
    Converter that encodes text to Base32 format.

    Base32 is a binary-to-text encoding defined in RFC 4648
    (https://www.rfc-editor.org/rfc/rfc4648). This converter takes input text,
    encodes its UTF-8 bytes, and returns the Base32 representation, which can be
    useful for obfuscating text or testing how systems handle encoded content.

    Two RFC 4648 alphabets are supported via the ``variant`` parameter:

    - ``"b32"`` (default): the standard Base32 alphabet (RFC 4648 section 6),
      backed by ``base64.b32encode``.
    - ``"b32hex"``: the Extended Hex Base32 alphabet (RFC 4648 section 7),
      backed by ``base64.b32hexencode``.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    Variant = Literal["b32", "b32hex"]

    def __init__(self, *, variant: Variant = "b32") -> None:
        """
        Initialize the Base32Converter.

        Args:
            variant: The RFC 4648 Base32 alphabet to use. ``"b32"`` (default) for the
                standard alphabet, ``"b32hex"`` for the Extended Hex alphabet.
        """
        self._variant = variant

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the selected variant.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "variant": self._variant,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to Base32 encoding.

        Args:
            prompt: The prompt to be converted.
            input_type: Type of data, unused for this converter.

        Returns:
            The converted text representation of the original prompt in Base32.

        Raises:
            ValueError: If the input type or variant is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        string_bytes = prompt.encode("utf-8")
        if self._variant == "b32":
            encoded_bytes = base64.b32encode(string_bytes)
        elif self._variant == "b32hex":
            encoded_bytes = base64.b32hexencode(string_bytes)
        else:
            raise ValueError("Unsupported Base32 variant")

        return ConverterResult(output_text=encoded_bytes.decode("utf-8"), output_type="text")
