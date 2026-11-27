# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType


class Base91Converter(Converter):
    """
    Converter that encodes text to basE91 format.

    basE91 is a binary-to-text encoding devised by Joachim Henke. It is more
    space-efficient than Base64 because it packs binary data into a 91-character
    alphabet (roughly 13-14 bits per two output characters), producing shorter
    output than Base64 for the same input. This can be useful for obfuscating
    text or testing how systems handle densely encoded content.

    The algorithm is implemented manually in pure Python (no third-party
    dependency), following the reference implementation.

    References:
        - Joachim Henke, basE91: http://base91.sourceforge.net/
        - https://en.wikipedia.org/wiki/Binary-to-text_encoding#basE91

    Alphabet (91 characters, indices 0-90)::

        ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&()*+,./:;<=>?@[]^_`{|}~"
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    # The 91-character basE91 alphabet, as defined by Joachim Henke's reference implementation.
    ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&()*+,./:;<=>?@[]^_`{|}~"'

    def __init__(self) -> None:
        """Initialize the Base91Converter."""

    @staticmethod
    def _base91_encode(data: bytes) -> str:
        """
        Encode raw bytes to a basE91 string using Joachim Henke's algorithm.

        Bits are accumulated little-endian in an integer bit buffer. Once more than
        13 bits are buffered (i.e. at least 14), the low 13 bits are inspected: if
        their value exceeds 88 they are emitted as a two-character group and 13 bits
        are consumed; otherwise the low 14 bits are emitted and 14 bits are consumed.
        Taking 14 bits only when the low 13 bits are 88 or less bounds the emitted
        value at ``91 * 91 - 1 = 8280``, so it always fits two base-91 digits. Each
        emitted value ``v`` becomes ``ALPHABET[v % 91] + ALPHABET[v // 91]``.

        Args:
            data: The bytes to encode.

        Returns:
            The basE91-encoded string.
        """
        alphabet = Base91Converter.ALPHABET
        bit_buffer = 0
        num_bits = 0
        output: list[str] = []

        for byte in data:
            bit_buffer |= byte << num_bits
            num_bits += 8
            if num_bits > 13:
                value = bit_buffer & 8191
                if value > 88:
                    bit_buffer >>= 13
                    num_bits -= 13
                else:
                    value = bit_buffer & 16383
                    bit_buffer >>= 14
                    num_bits -= 14
                output.append(alphabet[value % 91])
                output.append(alphabet[value // 91])

        # Flush any remaining bits.
        if num_bits:
            output.append(alphabet[bit_buffer % 91])
            if num_bits > 7 or bit_buffer > 90:
                output.append(alphabet[bit_buffer // 91])

        return "".join(output)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt to basE91 encoding.

        Args:
            prompt: The prompt to be converted.
            input_type: Type of data, unused for this converter.

        Returns:
            The converted text representation of the original prompt in basE91.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        encoded = self._base91_encode(prompt.encode("utf-8"))
        return ConverterResult(output_text=encoded, output_type="text")
