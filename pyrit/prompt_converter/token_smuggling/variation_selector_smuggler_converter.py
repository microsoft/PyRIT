# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal, Optional, Tuple

from pyrit.prompt_converter.token_smuggling.base import SmugglerConverter

logger = logging.getLogger(__name__)


class VariationSelectorSmugglerConverter(SmugglerConverter):
    """
    Encodes and decodes text using Unicode Variation Selectors.

    Each UTF-8 byte is mapped as follows:
        - Bytes 0x00-0x0F are mapped to U+FE00-U+FE0F.
        - Bytes 0x10-0xFF are mapped to U+E0100-U+E01EF.

    If ``embed_in_base`` is True, the payload is concatenated with a base character
    (default: 😊); otherwise, a space separator is inserted.

    Replicates functionality detailed in:
        - https://paulbutler.org/2025/smuggling-arbitrary-data-through-an-emoji/

    Extension: In addition to embedding into a base character, we also support
    appending invisible variation selectors directly to visible text—enabling mixed
    visible and hidden content within a single string.
    """

    def __init__(
        self,
        action: Literal["encode", "decode"] = "encode",
        base_char_utf8: Optional[str] = None,
        embed_in_base: bool = True,
    ):
        """
        Initialize the converter with options for encoding/decoding.

        Args:
            action (Literal["encode", "decode"]): The action to perform.
            base_char_utf8 (Optional[str]): Base character for ``variation_selector_smuggler`` mode (default: 😊).
            embed_in_base (bool): If True, the hidden payload is embedded directly into the base character.
                                    If False, a visible separator (space) is inserted between the base and payload.
                                    Default is True.

        Raises:
            ValueError: If an unsupported action or ``encoding_mode`` is provided.
        """
        super().__init__(action=action)
        self.utf8_base_char = base_char_utf8 if base_char_utf8 is not None else "😊"
        self.embed_in_base = embed_in_base

    def encode_message(self, message: str) -> Tuple[str, str]:
        """
        Encode the message using Unicode variation selectors.

        The message is converted to UTF-8 bytes, and each byte is mapped to a variation selector:
            - 0x00-0x0F => U+FE00 to U+FE0F.
            - 0x10-0xFF => U+E0100 to U+E01EF.

        If ``embed_in_base`` is True, the payload is embedded directly into the base character;
        otherwise, a visible separator (a space) is inserted between the base and payload.

        Args:
            message (str): The message to encode.

        Returns:
            Tuple[str, str]: A tuple containing a summary of the code points and the encoded string.
        """
        payload = ""
        data = message.encode("utf-8")
        for byte in data:
            if byte < 16:
                code_point = 0xFE00 + byte
            else:
                code_point = 0xE0100 + (byte - 16)
            payload += chr(code_point)
        if self.embed_in_base:
            encoded = self.utf8_base_char + payload
        else:
            encoded = self.utf8_base_char + " " + payload
        summary_parts = [f"Base char: U+{ord(self.utf8_base_char):X}"]
        for byte in data:
            if byte < 16:
                summary_parts.append(f"U+{(0xFE00 + byte):X}")
            else:
                summary_parts.append(f"U+{(0xE0100 + (byte - 16)):X}")
        code_points_summary = " ".join(summary_parts)
        logger.info(f"Variation Selector Smuggler encoding complete: {len(data)} bytes encoded.")
        return code_points_summary.strip(), encoded

    def decode_message(self, message: str) -> str:
        """
        Decode a message encoded using Unicode variation selectors.
        The decoder scans the string for variation selectors, ignoring any visible separator.

        Args:
            message (str): The encoded message.

        Returns:
            str: The decoded message.
        """
        bytes_out = bytearray()
        started = False
        for char in message:
            # If not embedding, skip visible separators (e.g., spaces)
            if not self.embed_in_base and char == " ":
                continue
            code = ord(char)
            if 0xFE00 <= code <= 0xFE0F:
                started = True
                byte = code - 0xFE00
                bytes_out.append(byte)
            elif 0xE0100 <= code <= 0xE01EF:
                started = True
                byte = (code - 0xE0100) + 16
                bytes_out.append(byte)
            else:
                if started:
                    break
        try:
            decoded_text = bytes_out.decode("utf-8")
        except UnicodeDecodeError:
            decoded_text = bytes_out.decode("utf-8", errors="replace")
            logger.error("Decoded byte sequence is not valid UTF-8; some characters may be replaced.")
        logger.info(f"Variation Selector Smuggler decoding complete: {len(decoded_text)} characters decoded.")
        return decoded_text

    # Extension of Paul Butler's method
    def encode_visible_hidden(self, visible: str, hidden: str) -> Tuple[str, str]:
        """
        Combine visible text with hidden text by encoding the hidden text using ``variation_selector_smuggler`` mode.

        The hidden payload is generated as a composite using the current embedding setting and then appended
        to the visible text.

        Args:
            visible (str): The visible text.
            hidden (str): The secret/hidden text to encode.

        Returns:
            Tuple[str, str]: A tuple containing a summary and the combined text.
        """
        summary, encoded_hidden = self.encode_message(hidden)
        combined = visible + encoded_hidden
        return summary, combined

    # Extension of Paul Butler's method
    def decode_visible_hidden(self, combined: str) -> Tuple[str, str]:
        """
        Extract the visible text and decodes the hidden text from a combined string.

        It searches for the first occurrence of the base character (``self.utf8_base_char``) and treats everything
        from that point on as the hidden payload.

        Args:
            combined (str): The combined text containing visible and hidden parts.

        Returns:
            Tuple[str, str]: A tuple with the visible text and the decoded hidden text.
        """
        base_char = self.utf8_base_char
        index = combined.find(base_char)
        if index == -1:
            return combined, ""
        visible = combined[:index]
        hidden_encoded = combined[index:]
        hidden = self.decode_message(hidden_encoded)
        return visible, hidden
