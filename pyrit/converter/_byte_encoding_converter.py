# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import binascii
from collections.abc import Callable
from typing import Literal

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class _ByteEncodingConverter(Converter):
    """
    Shared base for the standard "base-N" byte-to-text encodings.

    These encodings all follow the same shape: take the UTF-8 bytes of the prompt and run them
    through one of Python's standard-library ``base64``/``binascii`` encoders (base16, base32,
    base64, Ascii85/base85, ...). The concrete converter pins the ``encoding_func`` so selection is
    uniform across the whole family, and each keeps its own public identity (``Base64Converter``,
    ``Base32Converter``, ...).

    This base is intentionally *not* used by the custom-alphabet encoders (Base58/Base62/Base91),
    which are not standard-library byte encoders and remain standalone converters.
    """

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

    _ENCODERS: dict[str, Callable[[bytes], bytes]] = {
        "b64encode": base64.b64encode,
        "urlsafe_b64encode": base64.urlsafe_b64encode,
        "standard_b64encode": base64.standard_b64encode,
        "b2a_base64": binascii.b2a_base64,
        "b16encode": base64.b16encode,
        "b32encode": base64.b32encode,
        "a85encode": base64.a85encode,
        "b85encode": base64.b85encode,
    }

    def __init__(self, *, encoding_func: EncodingFunc = "b64encode") -> None:
        """
        Initialize the converter.

        Args:
            encoding_func: The standard-library byte encoder to use. Defaults to "b64encode".
        """
        self._encoding_func = encoding_func

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the selected encoding function.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "encoding_func": self._encoding_func,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to its byte-encoded representation.

        Args:
            prompt: The prompt to be converted.
            input_type: Type of data, unused for this converter.

        Returns:
            The converted text representation of the original prompt.

        Raises:
            ValueError: If the input type or encoding function is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        encoder = self._ENCODERS.get(self._encoding_func)
        if encoder is None:
            raise ValueError("Unsupported encoding function")

        encoded_bytes = encoder(prompt.encode("utf-8"))
        return ConverterResult(output_text=encoded_bytes.decode("utf-8"), output_type="text")
