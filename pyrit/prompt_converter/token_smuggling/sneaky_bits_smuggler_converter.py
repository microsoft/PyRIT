# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal, Optional, Tuple

from pyrit.prompt_converter.token_smuggling.base import SmugglerConverter

logger = logging.getLogger(__name__)


class SneakyBitsSmugglerConverter(SmugglerConverter):
    """
    Encodes and decodes text using a bit-level approach.

    Uses two invisible Unicode characters:
        - ``zero_char`` (default: U+2062) to represent binary 0.
        - ``one_char`` (default: U+2064) to represent binary 1.

    Replicates functionality detailed in:
        - https://embracethered.com/blog/posts/2025/sneaky-bits-and-ascii-smuggler/
    """

    def __init__(
        self,
        action: Literal["encode", "decode"] = "encode",
        zero_char: Optional[str] = None,
        one_char: Optional[str] = None,
    ):
        """
        Initialize the converter with options for encoding/decoding in Sneaky Bits mode.

        Args:
            action (Literal["encode", "decode"]): The action to perform.
            zero_char (Optional[str]): Character to represent binary 0 in ``sneaky_bits`` mode (default: U+2062).
            one_char (Optional[str]): Character to represent binary 1 in ``sneaky_bits`` mode (default: U+2064).

        Raises:
            ValueError: If an unsupported action or ``encoding_mode`` is provided.
        """
        super().__init__(action=action)
        self.zero_char = zero_char if zero_char is not None else "\u2062"  # Invisible Times
        self.one_char = one_char if one_char is not None else "\u2064"  # Invisible Plus

    def encode_message(self, message: str) -> Tuple[str, str]:
        """
        Encode the message using Sneaky Bits mode.

        The message is first converted to its UTF-8 byte sequence. Then each byte is represented as 8 bits,
        with each bit replaced by an invisible character (``self.zero_char`` for 0 and ``self.one_char`` for 1).

        Args:
            message (str): The message to encode.

        Returns:
            Tuple[str, str]: A tuple where the first element is a bit summary (empty in this implementation)
            and the second element is the encoded message containing the invisible bits.
        """
        encoded_bits = []
        data = message.encode("utf-8")
        for byte in data:
            for bit_index in range(7, -1, -1):
                bit = (byte >> bit_index) & 1
                encoded_bits.append(self.zero_char if bit == 0 else self.one_char)
        encoded_text = "".join(encoded_bits)
        # For Sneaky Bits, we do not provide a code point summary.
        bit_summary = ""
        logger.info(
            f"Sneaky Bits encoding complete: {len(message)} characters encoded into {len(encoded_text)} invisible bits."
        )
        return bit_summary, encoded_text

    def decode_message(self, message: str) -> str:
        """
        Decode the message encoded using Sneaky Bits mode.

        The method filters out only the valid invisible characters (``self.zero_char`` and ``self.one_char``),
        groups them into 8-bit chunks, reconstructs each byte, and finally decodes the byte sequence using UTF-8.

        Args:
            message (str): The message encoded with Sneaky Bits.

        Returns:
            str: The decoded original message.
        """
        # Filter only the valid bit characters.
        bit_chars = [c for c in message if c == self.zero_char or c == self.one_char]
        bit_count = len(bit_chars)
        if bit_count % 8 != 0:
            logger.warning("Encoded bit string length is not a multiple of 8. Ignoring incomplete trailing bits.")
            bit_count -= bit_count % 8
            bit_chars = bit_chars[:bit_count]

        decoded_bytes = bytearray()
        current_byte = 0
        bits_collected = 0
        for c in bit_chars:
            current_byte = (current_byte << 1) | (1 if c == self.one_char else 0)
            bits_collected += 1
            if bits_collected == 8:
                decoded_bytes.append(current_byte)
                current_byte = 0
                bits_collected = 0

        try:
            decoded_text = decoded_bytes.decode("utf-8")
        except UnicodeDecodeError:
            decoded_text = decoded_bytes.decode("utf-8", errors="replace")
            logger.error("Decoded byte sequence is not valid UTF-8; some characters may be replaced.")

        logger.info(f"Sneaky Bits decoding complete: Decoded text length is {len(decoded_text)} characters.")
        return decoded_text
