# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal

from pyrit.converter._base_n import encode_base_n
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

# Base58 alphabets. The default is the Bitcoin alphabet, which omits the four
# visually ambiguous characters 0 (zero), O (capital o), I (capital i) and
# l (lowercase L). The Flickr alphabet is the same 58 characters re-ordered so
# that lowercase letters precede uppercase letters.
# See: https://en.wikipedia.org/wiki/Binary-to-text_encoding#Base58 and the
# Bitcoin Base58Check reference (https://en.bitcoin.it/wiki/Base58Check_encoding).
_BITCOIN_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_FLICKR_ALPHABET = "123456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"

_ALPHABETS: dict[str, str] = {
    "bitcoin": _BITCOIN_ALPHABET,
    "flickr": _FLICKR_ALPHABET,
}


class Base58Converter(Converter):
    """
    Converter that encodes text to Base58 format.

    Base58 is a binary-to-text encoding devised for Bitcoin addresses. It uses an
    alphabet of 58 characters, chosen by omitting the four characters that are easy
    to confuse visually (``0`` zero, ``O`` capital o, ``I`` capital i and ``l``
    lowercase L) as well as the non-alphanumeric ``+`` and ``/`` used by Base64.
    Unlike Base64, Base58 is not a simple block transform: the whole input is treated
    as a single big-endian integer and repeatedly divided by 58, with each leading
    zero byte rendered as the first alphabet character.

    This implementation is pure Python (no third-party dependency). It supports the
    default Bitcoin alphabet and the Flickr alphabet variant.

    References:
        - Bitcoin Base58Check encoding: https://en.bitcoin.it/wiki/Base58Check_encoding
        - Binary-to-text encoding (Base58): https://en.wikipedia.org/wiki/Binary-to-text_encoding#Base58
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    Alphabet = Literal["bitcoin", "flickr"]

    def __init__(self, *, alphabet: Alphabet = "bitcoin") -> None:
        """
        Initialize the Base58Converter.

        Args:
            alphabet: The Base58 alphabet to use, either ``"bitcoin"`` (default) or
                ``"flickr"``.

        Raises:
            ValueError: If an unsupported alphabet name is provided.
        """
        if alphabet not in _ALPHABETS:
            raise ValueError(f"Unsupported alphabet '{alphabet}'. Supported: {sorted(_ALPHABETS)}")
        self._alphabet_name = alphabet
        self._alphabet = _ALPHABETS[alphabet]

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the selected alphabet.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "alphabet": self._alphabet_name,
            },
        )

    def _encode(self, data: bytes) -> str:
        """
        Encode raw bytes to a Base58 string using the configured alphabet.

        Args:
            data: The bytes to encode.

        Returns:
            str: The Base58-encoded representation.
        """
        return encode_base_n(data, alphabet=self._alphabet)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to Base58 encoding.

        Args:
            prompt: The prompt to be converted.
            input_type: Type of data, unused for this converter.

        Returns:
            The converted text representation of the original prompt in Base58.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        encoded = self._encode(prompt.encode("utf-8"))
        return ConverterResult(output_text=encoded, output_type="text")
