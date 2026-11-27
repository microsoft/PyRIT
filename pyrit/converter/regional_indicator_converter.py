# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType


class RegionalIndicatorConverter(Converter):
    """
    Converts ASCII letters to Unicode Regional Indicator Symbols (the "flag letters").

    Each letter A-Z (case-insensitive) is mapped to the corresponding code point in the
    Regional Indicator Symbol block, U+1F1E6 (REGIONAL INDICATOR SYMBOL LETTER A) through
    U+1F1FF (REGIONAL INDICATOR SYMBOL LETTER Z). These square letter tiles were introduced
    to encode ISO 3166-1 region codes: rendered in pairs they become emoji flags (for
    example "US" -> the flag of the United States), but a single tile displays as a
    boxed capital letter.

    To keep the output readable letter-by-letter rather than collapsing consecutive pairs
    into flag emoji, a ZERO WIDTH NON-JOINER (U+200C) is inserted between two adjacent
    regional indicators whenever at least one of them was produced by this converter. The
    joiner is invisible and breaks the flag sequence defined in UTS #51, so "US" renders as
    two boxed letters instead of one flag. Two regional indicators that were both already in
    the input (an existing flag) are left joined, so pre-existing flags are preserved.
    Non-letter characters (digits, punctuation, spaces, and anything outside the Latin
    alphabet) are left unchanged, which also naturally breaks any flag run.

    This is a purely typographic transform with no formal academic source; it was
    popularized as an emoji-flag obfuscation trick, in the spirit of the P4RS3LT0NGV3
    (parseltongue) toolkit: https://github.com/BASI-LABS/parseltongue

    Note: This converter is useful for testing how AI systems handle text disguised as
    emoji-flag tiles, which can be used to obfuscate potentially harmful content.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    #: First code point of the Regional Indicator Symbol block (letter A).
    _REGIONAL_INDICATOR_A = 0x1F1E6
    #: Last code point of the Regional Indicator Symbol block (letter Z).
    _REGIONAL_INDICATOR_Z = 0x1F1FF
    #: Zero-width non-joiner inserted between adjacent tiles to prevent flag formation.
    _SEPARATOR = "‌"

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given text into Regional Indicator Symbols.

        Args:
            prompt (str): The text to convert.
            input_type (PromptDataType, optional): Type of input data. Defaults to "text".

        Returns:
            ConverterResult: The text with ASCII letters replaced by regional indicators.

        Raises:
            ValueError: If the input type is not supported (only "text" is supported).
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        result: list[str] = []
        # Track the previously emitted character: whether it was a regional indicator at all, and
        # whether that indicator was one this converter produced (vs. already present in the input).
        previous_was_indicator = False
        previous_was_converted = False
        for char in prompt:
            if "a" <= char.lower() <= "z" and char.isascii():
                emitted = chr(self._REGIONAL_INDICATOR_A + ord(char.lower()) - ord("a"))
                is_indicator = True
                is_converted = True
            else:
                emitted = char
                is_converted = False
                is_indicator = self._REGIONAL_INDICATOR_A <= ord(char) <= self._REGIONAL_INDICATOR_Z

            # Separate two adjacent indicators only when at least one was produced here, so a newly
            # converted letter cannot form a flag with a neighbor. Two indicators that were both
            # already in the input (an existing flag) are left joined.
            if is_indicator and previous_was_indicator and (is_converted or previous_was_converted):
                result.append(self._SEPARATOR)

            result.append(emitted)
            previous_was_indicator = is_indicator
            previous_was_converted = is_converted and is_indicator

        return ConverterResult(output_text="".join(result), output_type="text")
