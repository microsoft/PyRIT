# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from pyrit.prompt_converter.token_smuggling.base import SmugglerConverter

logger = logging.getLogger(__name__)


class AsciiSmugglerConverter(SmugglerConverter):
    """
    Implements encoding and decoding using Unicode Tags.

    If 'control' is True, the encoded output is wrapped with:
        - U+E0001 (start control tag)
        - U+E007F (end control tag)

    Replicates the functionality detailed in the following blog post:
    https://embracethered.com/blog/posts/2024/hiding-and-finding-text-with-unicode-tags/
    """

    def __init__(self, action: Literal["encode", "decode"] = "encode", unicode_tags: bool = False):
        """
        Initialize the converter with options for encoding/decoding.

        Args:
            action (Literal["encode", "decode"]): The action to perform.
            unicode_tags (bool): Whether to add Unicode tags during encoding.
        """
        self.unicode_tags = unicode_tags
        super().__init__(action=action)

    def encode_message(self, *, message: str) -> tuple[str, str]:
        """
        Encode the message using Unicode Tags.

        Each ASCII printable character (0x20-0x7E) is mapped to a corresponding
        Unicode Tag (by adding 0xE0000). If control mode is enabled, wraps the output.

        Args:
            message (str): The message to encode.

        Returns:
            Tuple[str, str]: A tuple with a summary of code points and the encoded message.
        """
        encoded = ""
        code_points = ""
        invalid_chars = ""
        if self.unicode_tags:
            encoded += chr(0xE0001)
            code_points += "U+E0001 "
        for char in message:
            if 0x20 <= ord(char) <= 0x7E:
                code_point = 0xE0000 + ord(char)
                encoded += chr(code_point)
                code_points += f"U+{code_point:X} "
            else:
                invalid_chars += char
        if self.unicode_tags:
            encoded += chr(0xE007F)
            code_points += "U+E007F"

        if invalid_chars:
            logger.error(f"Invalid characters detected: {invalid_chars}")
        return code_points, encoded

    def decode_message(self, *, message: str) -> str:
        """
        Decode a message encoded with Unicode Tags.

        For each character in the Unicode Tags range, subtracts 0xE0000.
        Skips control tags if present.

        Args:
            message (str): The encoded message.

        Returns:
            str: The decoded message.
        """
        decoded_message = ""
        for char in message:
            code_point = ord(char)
            if 0xE0000 <= code_point <= 0xE007F:
                decoded_char = chr(code_point - 0xE0000)
                if not 0x20 <= ord(decoded_char) <= 0x7E:
                    logger.info(f"Potential unicode tag detected: {decoded_char}")
                else:
                    decoded_message += decoded_char

        if len(decoded_message) != len(message):
            logger.info("Hidden Unicode Tags discovered.")
        else:
            logger.info("No hidden Unicode Tag characters discovered.")
        return decoded_message
