# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import unicodedata
from typing import Literal

from pyrit.converter.token_smuggling.base import SmugglerConverter
from pyrit.models import ComponentIdentifier

logger = logging.getLogger(__name__)

# The England, Scotland and Wales flags are the three RGI emoji tag sequences: U+1F3F4, the subdivision id in tag
# characters, then U+E007F. They are visible emoji, so the decoder must not read them as hidden text. Only these
# exact sequences are skipped; any other tag text, including a look-alike flag, is still decoded.
_SUBDIVISION_FLAG_SEQUENCES = tuple(
    "\U0001f3f4" + "".join(chr(0xE0000 + ord(char)) for char in subdivision) + "\U000e007f"
    for subdivision in ("gbeng", "gbsct", "gbwls")
)


class AsciiSmugglerConverter(SmugglerConverter):
    """
    Implements encoding and decoding using Unicode Tags.

    If 'control' is True, the encoded output is wrapped with:
        - U+E0001 (start control tag)
        - U+E007F (end control tag)

    Unicode Tags only exist for printable ASCII, so before encoding, characters with a faithful ASCII
    equivalent are replaced: whitespace such as newlines and tabs becomes a space, typographic dashes,
    quotes and ellipses become their ASCII forms, and accents are removed (``é`` becomes ``e``).
    Characters with no ASCII equivalent, such as CJK text or emoji, still raise ``ValueError``.

    Replicates the functionality detailed in the following blog post:
    [@embracethered2024unicode]
    """

    _ASCII_EQUIVALENTS: dict[int, str] = {
        **dict.fromkeys(map(ord, "\t\n\v\f\r\x85\xa0\u1680\u2028\u2029\u202f\u205f\u3000"), " "),
        **dict.fromkeys(range(0x2000, 0x200B), " "),
        **dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-"),
        **dict.fromkeys(map(ord, "\u2018\u2019\u201a\u201b\u2032"), "'"),
        **dict.fromkeys(map(ord, "\u201c\u201d\u201e\u201f\u2033"), '"'),
        ord("\u2026"): "...",
    }

    def __init__(self, *, action: Literal["encode", "decode"] = "encode", unicode_tags: bool = False) -> None:
        """
        Initialize the converter with options for encoding/decoding.

        Args:
            action (Literal["encode", "decode"]): The action to perform.
            unicode_tags (bool): Whether to add Unicode tags during encoding.
        """
        self.unicode_tags = unicode_tags
        super().__init__(action=action)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with ASCII smuggler parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "action": self.action,
                "unicode_tags": self.unicode_tags,
            }
        )

    def encode_message(self, *, message: str) -> tuple[str, str]:
        """
        Encode the message using Unicode Tags.

        Characters with a faithful ASCII equivalent are first replaced with it. Each ASCII printable
        character (0x20-0x7E) is then mapped to a corresponding Unicode Tag (by adding 0xE0000).
        If control mode is enabled, wraps the output.

        Args:
            message (str): The message to encode.

        Returns:
            tuple[str, str]: A tuple with a summary of code points and the encoded message.

        Raises:
            ValueError: If ``message`` contains a character with no ASCII printable (0x20-0x7E) equivalent,
                which has no Unicode Tag representation. Such characters were previously dropped while the
                result still reported success; use ``SneakyBitsSmugglerConverter`` or
                ``VariationSelectorSmugglerConverter`` to smuggle arbitrary Unicode losslessly.
        """
        ascii_message = self.to_ascii_equivalent(text=message)
        if ascii_message is None:
            invalid_chars = "".join(char for char in message if self._ascii_equivalent(char) is None)
            raise ValueError(
                f"Cannot encode {len(invalid_chars)} character(s) with no equivalent in the ASCII printable range "
                f"(0x20-0x7E): {invalid_chars!r}. Use SneakyBitsSmugglerConverter or "
                f"VariationSelectorSmugglerConverter for lossless Unicode smuggling."
            )

        message = ascii_message
        encoded = ""
        code_points = ""
        if self.unicode_tags:
            encoded += chr(0xE0001)
            code_points += "U+E0001 "
        for char in message:
            code_point = 0xE0000 + ord(char)
            encoded += chr(code_point)
            code_points += f"U+{code_point:X} "
        if self.unicode_tags:
            encoded += chr(0xE007F)
            code_points += "U+E007F"
        return code_points, encoded

    @classmethod
    def to_ascii_equivalent(cls, *, text: str) -> str | None:
        """
        Return the printable ASCII text that encoding ``text`` actually smuggles.

        Args:
            text (str): The text to normalize.

        Returns:
            str | None: ``text`` with each character replaced by its ASCII equivalent, or None when any
                character has no equivalent and encoding would raise.
        """
        equivalents = [cls._ascii_equivalent(char) for char in text]
        if any(equivalent is None for equivalent in equivalents):
            return None
        return "".join(equivalent for equivalent in equivalents if equivalent is not None)

    @classmethod
    def _ascii_equivalent(cls, char: str) -> str | None:
        """
        Return the printable ASCII text that faithfully represents ``char``.

        Args:
            char (str): A single character.

        Returns:
            str | None: The ASCII replacement, or None when ``char`` has no non-empty printable ASCII equivalent.
        """
        if 0x20 <= ord(char) <= 0x7E:
            return char
        replacement = cls._ASCII_EQUIVALENTS.get(ord(char))
        if replacement is None:
            replacement = "".join(
                decomposed
                for decomposed in unicodedata.normalize("NFKD", char)
                if not unicodedata.combining(decomposed)
            )
        if replacement and all(0x20 <= ord(ascii_char) <= 0x7E for ascii_char in replacement):
            return replacement
        return None

    def decode_message(self, *, message: str) -> str:
        """
        Decode a message encoded with Unicode Tags.

        For each character in the Unicode Tags range, subtracts 0xE0000.
        Skips control tags if present, and the England, Scotland and Wales flag emoji, which are written with tags.

        Args:
            message (str): The encoded message.

        Returns:
            str: The decoded message.
        """
        # A space, not an empty string, so that removing one flag cannot join its neighbours into another flag.
        for flag in _SUBDIVISION_FLAG_SEQUENCES:
            message = message.replace(flag, " ")

        decoded_message = ""
        found_tag = False
        for char in message:
            code_point = ord(char)
            if 0xE0000 <= code_point <= 0xE007F:
                found_tag = True
                decoded_char = chr(code_point - 0xE0000)
                if not 0x20 <= ord(decoded_char) <= 0x7E:
                    logger.info(f"Potential unicode tag detected: {decoded_char}")
                else:
                    decoded_message += decoded_char

        # A message that is entirely hidden tags decodes to the same length as the input, so
        # comparing lengths misreports it as having nothing hidden. Track whether a tag code
        # point was actually seen instead.
        if found_tag:
            logger.info("Hidden Unicode Tags discovered.")
        else:
            logger.info("No hidden Unicode Tag characters discovered.")
        return decoded_message
