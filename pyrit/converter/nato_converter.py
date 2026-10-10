# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter.word_level_converter import WordLevelConverter


class NatoConverter(WordLevelConverter):
    """
    Converts text into NATO phonetic alphabet representation.

    This converter transforms standard text into NATO phonetic alphabet format,
    where each ASCII letter is replaced with its corresponding NATO phonetic code
    word (e.g., "A" becomes "Alfa", "B" becomes "Bravo"). Characters outside the
    ASCII alphabet are preserved with their original casing. Spaces are represented
    by ``<space>`` so word boundaries remain distinct from code-word separators.

    The NATO phonetic alphabet is the most widely used spelling alphabet, designed
    to improve clarity of voice communication. This converter can be used to test
    how AI systems handle phonetically encoded text, which can be used to obfuscate
    potentially harmful content.

    Reference: https://en.wikipedia.org/wiki/NATO_phonetic_alphabet

    Example:
        Input: "Hello world"
        Output: "Hotel Echo Lima Lima Oscar <space> Whiskey Oscar Romeo Lima Delta"
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    _WORD_SEPARATOR = "<space>"

    _NATO_MAP = {
        "A": "Alfa",
        "B": "Bravo",
        "C": "Charlie",
        "D": "Delta",
        "E": "Echo",
        "F": "Foxtrot",
        "G": "Golf",
        "H": "Hotel",
        "I": "India",
        "J": "Juliett",
        "K": "Kilo",
        "L": "Lima",
        "M": "Mike",
        "N": "November",
        "O": "Oscar",
        "P": "Papa",
        "Q": "Quebec",
        "R": "Romeo",
        "S": "Sierra",
        "T": "Tango",
        "U": "Uniform",
        "V": "Victor",
        "W": "Whiskey",
        "X": "Xray",
        "Y": "Yankee",
        "Z": "Zulu",
    }

    async def convert_word_async(self, word: str) -> str:
        """
        Convert one word into NATO phonetic alphabet representation.

        Args:
            word (str): The word to convert.

        Returns:
            str: The converted word, with code words separated by spaces.
        """
        output = [self._NATO_MAP.get(char.upper(), char) if char.isascii() else char for char in word]
        return " ".join(output)

    def join_words(self, words: list[str]) -> str:
        """
        Join converted words while preserving every source-space boundary.

        Args:
            words (list[str]): The converted words.

        Returns:
            str: The converted words separated by explicit space tokens.
        """
        output: list[str] = []
        for index, word in enumerate(words):
            if index:
                output.append(self._WORD_SEPARATOR)
            if word:
                output.append(word)

        return " ".join(output)
