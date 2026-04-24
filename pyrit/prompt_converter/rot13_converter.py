# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import codecs

from pyrit.prompt_converter.word_level_converter import WordLevelConverter


class ROT13Converter(WordLevelConverter):
    """
    Encodes prompts using the ROT13 cipher.
    """

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        return codecs.encode(word, "rot13")
