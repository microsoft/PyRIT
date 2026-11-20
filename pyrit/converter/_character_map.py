# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared base for simple character-map converters (a lookup table, optionally case-folded/reversed).

Runic, Ogham, Mirror and UpsideDown are all the same shape: replace each character via a lookup
table (characters absent from the table pass through unchanged), optionally looking the table up
case-insensitively, and optionally reversing the whole string afterwards (for mirror / turned
text). This base owns that loop so each converter only has to declare its table and two flags.

This module is intentionally private (underscore prefix): it is an internal helper shared by the
character-map converters, not part of the public ``pyrit.converter`` API.
"""

from typing import ClassVar

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType


class CharacterMapConverter(Converter):
    """
    Base class for converters that transform text through a per-character lookup table.

    Subclasses set:

    - ``_CHAR_MAP`` — the character-to-replacement table; characters not present pass through
      unchanged.
    - ``_CASE_INSENSITIVE`` — when True, the table is looked up by the lower-cased character (the
      original character is still used as the pass-through fallback).
    - ``_REVERSE_OUTPUT`` — when True, the whole mapped string is reversed (used by mirror/turned
      text so the result reads as the original, flipped).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    #: Character -> replacement string. Unmapped characters are passed through unchanged.
    _CHAR_MAP: ClassVar[dict[str, str]] = {}
    #: Look the table up by the lower-cased character.
    _CASE_INSENSITIVE: ClassVar[bool] = False
    #: Reverse the mapped string (mirror / upside-down text).
    _REVERSE_OUTPUT: ClassVar[bool] = False

    def _map_text(self, text: str) -> str:
        """
        Apply the character map (and optional reversal) to ``text``.

        Args:
            text (str): The input text.

        Returns:
            str: The mapped text, reversed when ``_REVERSE_OUTPUT`` is set.
        """
        result = [self._CHAR_MAP.get(char.lower() if self._CASE_INSENSITIVE else char, char) for char in text]
        if self._REVERSE_OUTPUT:
            result.reverse()
        return "".join(result)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt through the character map.

        Args:
            prompt (str): The input prompt to be converted.
            input_type (PromptDataType): The type of the input prompt. Must be "text".

        Returns:
            ConverterResult: The result containing the converted prompt and its type.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        return ConverterResult(output_text=self._map_text(prompt), output_type="text")
