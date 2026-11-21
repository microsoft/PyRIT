# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._base_n import encode_base_n
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType

# Base62 alphabet (0-9, A-Z, a-z). See https://en.wikipedia.org/wiki/Base62
# This is the most common ("GMP-style") ordering used by Base62 encoders.
_BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class Base62Converter(Converter):
    """
    Converter that encodes text to a Base62 string.

    Base62 represents binary data using the 62 alphanumeric characters
    (``0-9``, ``A-Z``, ``a-z``), avoiding the ``+``, ``/`` and ``=`` characters of
    Base64. See https://en.wikipedia.org/wiki/Base62 for the scheme.

    This implementation is pure Python (no third-party dependency). The UTF-8
    bytes of the input are interpreted as a single big-endian integer and
    repeatedly divided by 62 to produce digits in the alphabet
    ``0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz`` (index 0
    maps to ``"0"``, index 61 maps to ``"z"``). Because big-integer encoding does
    not preserve leading zero bytes positionally, each leading ``0x00`` byte is
    emitted as a leading ``"0"`` so that round-tripping is lossless.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    @staticmethod
    def _encode(data: bytes) -> str:
        """
        Encode raw bytes to a Base62 string.

        Args:
            data: The bytes to encode.

        Returns:
            The Base62 encoded string.
        """
        return encode_base_n(data, alphabet=_BASE62_ALPHABET)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to a Base62 encoded string.

        Args:
            prompt: The prompt to be converted.
            input_type: Type of data, must be "text".

        Returns:
            The Base62 encoded representation of the UTF-8 bytes of the prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        encoded = self._encode(prompt.encode("utf-8"))
        return ConverterResult(output_text=encoded, output_type="text")
