# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import unicodedata

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType

logger = logging.getLogger(__name__)


def _build_isolated_form_map() -> dict[str, str]:
    """
    Build a mapping from standard Arabic letters to their isolated presentation forms.

    The map is derived from the Unicode compatibility decompositions of the Arabic
    Presentation Forms-B block (U+FE70-U+FEFF) rather than hand-transcribed, so it always
    reflects the Unicode data shipped with the running interpreter. Ligatures that decompose
    to more than one base letter (for example LAM WITH ALEF) are skipped.

    Returns:
        dict[str, str]: Mapping of base Arabic letter to its isolated presentation form.
    """
    mapping: dict[str, str] = {}
    for code_point in range(0xFE70, 0xFF00):
        char = chr(code_point)
        decomposition = unicodedata.decomposition(char)
        if not decomposition.startswith("<isolated>"):
            continue
        parts = decomposition.split()
        if len(parts) != 2:  # Skip ligatures that decompose to multiple base letters
            continue
        base = chr(int(parts[1], 16))
        mapping[base] = char
    return mapping


_ARABIC_TO_ISOLATED: dict[str, str] = _build_isolated_form_map()


class ArabicPresentationFormConverter(Converter):
    """
    Substitutes Arabic letters with their isolated Arabic Presentation Forms-B glyphs.

    Each standard Arabic letter is replaced by its isolated presentation form (for example ALEF
    U+0627 is replaced by U+FE8D). A reader still recognizes the same letters, shown in their
    non-joining isolated shapes, while the underlying code point and token sequence change. The
    substitution map is derived from Unicode decomposition data, so it stays correct without a
    hand-maintained table. The transformation is deterministic, and characters with no Arabic
    isolated presentation form (including Arabic digits and punctuation) are left unchanged.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by substituting Arabic letters with isolated presentation forms.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the substituted text.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        converted_text = "".join(_ARABIC_TO_ISOLATED.get(char, char) for char in prompt)
        return ConverterResult(output_text=converted_text, output_type="text")
