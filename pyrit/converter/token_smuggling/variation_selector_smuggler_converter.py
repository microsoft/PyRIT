# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from pyrit.converter.token_smuggling.base import SmugglerConverter
from pyrit.models import ComponentIdentifier

logger = logging.getLogger(__name__)


def _is_variation_selector(char: str) -> bool:
    code_point = ord(char)
    return 0xFE00 <= code_point <= 0xFE0F or 0xE0100 <= code_point <= 0xE01EF


class VariationSelectorSmugglerConverter(SmugglerConverter):
    """
    Encodes and decodes text using Unicode Variation Selectors.

    Each UTF-8 byte is mapped as follows:
        - Bytes 0x00-0x0F are mapped to U+FE00-U+FE0F.
        - Bytes 0x10-0xFF are mapped to U+E0100-U+E01EF.

    If ``embed_in_base`` is True, the payload is concatenated with a base character
    (default: 😊); otherwise, a space separator is inserted. A one-byte payload of
    0x0E or 0x0F always uses the separator to distinguish it from an emoji's presentation selector.

    Replicates functionality detailed in:
        - https://paulbutler.org/2025/smuggling-arbitrary-data-through-an-emoji/

    Extension: In addition to embedding into a base character, we also support
    appending invisible variation selectors directly to visible text—enabling mixed
    visible and hidden content within a single string.
    """

    _PRESENTATION_SELECTORS = ("\ufe0e", "\ufe0f")

    def __init__(
        self,
        *,
        action: Literal["encode", "decode"] = "encode",
        base_char_utf8: str | None = None,
        embed_in_base: bool = True,
    ) -> None:
        """
        Initialize the converter with options for encoding/decoding.

        Args:
            action (Literal["encode", "decode"]): The action to perform.
            base_char_utf8 (str | None): Base character for ``variation_selector_smuggler`` mode (default: 😊).
            embed_in_base (bool): If True, the hidden payload is embedded directly into the base character.
                                    If False, a visible separator (space) is inserted between the base and payload.
                                    Default is True.

        Raises:
            ValueError: If an unsupported action is provided, or ``base_char_utf8`` is not exactly one character
                or is a variation selector used to encode payload bytes.
        """
        super().__init__(action=action)
        base_char = base_char_utf8 if base_char_utf8 is not None else "😊"
        if len(base_char) != 1:
            raise ValueError("base_char_utf8 must be exactly one character.")
        if _is_variation_selector(base_char):
            raise ValueError("base_char_utf8 must not be a variation selector.")
        self.utf8_base_char = base_char
        self.embed_in_base = embed_in_base

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with variation selector parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "action": self.action,
                "base_char": self.utf8_base_char,
                "embed_in_base": self.embed_in_base,
            }
        )

    def encode_message(self, message: str) -> tuple[str, str]:
        """
        Encode the message using Unicode variation selectors.

        The message is converted to UTF-8 bytes, and each byte is mapped to a variation selector:
            - 0x00-0x0F => U+FE00 to U+FE0F.
            - 0x10-0xFF => U+E0100 to U+E01EF.

        If ``embed_in_base`` is True, the payload is embedded directly into the base character;
        otherwise, a visible separator (a space) is inserted between the base and payload.
        One-byte 0x0E and 0x0F payloads always use the separator because their selectors also
        control emoji presentation.

        Args:
            message (str): The message to encode.

        Returns:
            tuple[str, str]: A tuple containing a summary of the code points and the encoded string.
        """
        payload = ""
        data = message.encode("utf-8")
        for byte in data:
            code_point = 65024 + byte if byte < 16 else 917760 + (byte - 16)
            payload += chr(code_point)
        use_separator = not self.embed_in_base or payload in self._PRESENTATION_SELECTORS
        encoded = self.utf8_base_char + (" " if use_separator else "") + payload
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

        The decoder reads the variation selectors that follow the base character, with or without the
        space separator. Selectors elsewhere in the text, such as the U+FE0F in an emoji like ❤️, are not part
        of the payload. A lone U+FE0E or U+FE0F directly adjacent to the base character is ambiguous
        with emoji presentation; a space separator identifies it as a payload. If no unambiguous
        anchored payload is found, the first run of variation selectors is decoded for compatibility.

        Args:
            message (str): The encoded message.

        Returns:
            str: The decoded message.
        """
        start = message.find(self.utf8_base_char)
        while start != -1:
            payload_start = start + 1
            has_separator = message[payload_start : payload_start + 1] == " "
            if has_separator:
                payload_start += 1
            payload_end = payload_start
            while payload_end < len(message) and _is_variation_selector(message[payload_end]):
                payload_end += 1
            payload = message[payload_start:payload_end]
            if payload and (has_separator or payload not in self._PRESENTATION_SELECTORS):
                message = message[payload_start:]
                break
            start = message.find(self.utf8_base_char, start + 1)

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
    def encode_visible_hidden(self, visible: str, hidden: str) -> tuple[str, str]:
        """
        Combine visible text with hidden text by encoding the hidden text using ``variation_selector_smuggler`` mode.

        The hidden payload is generated as a composite using the current embedding setting and then appended
        to the visible text.

        Args:
            visible (str): The visible text.
            hidden (str): The secret/hidden text to encode.

        Returns:
            tuple[str, str]: A tuple containing a summary and the combined text.
        """
        summary, encoded_hidden = self.encode_message(hidden)
        combined = visible + encoded_hidden
        return summary, combined

    # Extension of Paul Butler's method
    def decode_visible_hidden(self, combined: str) -> tuple[str, str]:
        """
        Extract the visible text and decodes the hidden text from a combined string.

        ``encode_visible_hidden`` appends the base character and the hidden payload after the visible text, so the
        last occurrence of the base character starts the hidden payload. The visible text may contain the base
        character itself.

        Args:
            combined (str): The combined text containing visible and hidden parts.

        Returns:
            tuple[str, str]: A tuple with the visible text and the decoded hidden text.
        """
        index = combined.rfind(self.utf8_base_char)
        has_separator = not self.embed_in_base or combined[index + 1 :] in self._PRESENTATION_SELECTORS
        if has_separator and index > 0 and combined[index - 1] == self.utf8_base_char == " ":
            # With a space as the base character, the last space found is the separator after it.
            index -= 1
        if index == -1:
            return combined, ""
        visible = combined[:index]
        hidden_encoded = combined[index:]
        hidden = self.decode_message(hidden_encoded)
        return visible, hidden
