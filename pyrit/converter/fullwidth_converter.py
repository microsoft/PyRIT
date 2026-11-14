# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter.word_level_converter import WordLevelConverter


class FullwidthConverter(WordLevelConverter):
    """
    Converts printable ASCII characters to their fullwidth Unicode forms.

    Letters, digits and punctuation in the range U+0021 to U+007E map to the Halfwidth and Fullwidth Forms block
    (U+FF01 to U+FF5E), so "Hello!" becomes "Ｈｅｌｌｏ！". Characters outside that range, including spaces between
    words, are left unchanged. The output normalizes back to the original text under NFKC, which makes this a
    useful probe for filters or classifiers that match on exact characters without normalizing the input first.
    """

    _FULLWIDTH_OFFSET = 0xFEE0
    _ASCII_PRINTABLE_START = 0x21
    _ASCII_PRINTABLE_END = 0x7E

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into its fullwidth form.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The word with printable ASCII characters replaced by fullwidth characters.
        """
        return "".join(
            chr(ord(char) + self._FULLWIDTH_OFFSET)
            if self._ASCII_PRINTABLE_START <= ord(char) <= self._ASCII_PRINTABLE_END
            else char
            for char in word
        )
