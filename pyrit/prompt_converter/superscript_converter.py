# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.prompt_converter.word_level_converter import WordLevelConverter


class SuperscriptConverter(WordLevelConverter):
    """
    Converts text to superscript.

    This converter leaves characters that do not have a superscript equivalent unchanged.
    """

    _superscript_map = {
        "0": "\u2070",
        "1": "\u00b9",
        "2": "\u00b2",
        "3": "\u00b3",
        "4": "\u2074",
        "5": "\u2075",
        "6": "\u2076",
        "7": "\u2077",
        "8": "\u2078",
        "9": "\u2079",
        "a": "\u1d43",
        "b": "\u1d47",
        "c": "\u1d9c",
        "d": "\u1d48",
        "e": "\u1d49",
        "f": "\u1da0",
        "g": "\u1d4d",
        "h": "\u02b0",
        "i": "\u2071",
        "j": "\u02b2",
        "k": "\u1d4f",
        "l": "\u02e1",
        "m": "\u1d50",
        "n": "\u207f",
        "o": "\u1d52",
        "p": "\u1d56",
        "r": "\u02b3",
        "s": "\u02e2",
        "t": "\u1d57",
        "u": "\u1d58",
        "v": "\u1d5b",
        "w": "\u02b7",
        "x": "\u02e3",
        "y": "\u02b8",
        "z": "\u1dbb",
        "A": "\u1d2c",
        "B": "\u1d2d",
        "D": "\u1d30",
        "E": "\u1d31",
        "G": "\u1d33",
        "H": "\u1d34",
        "I": "\u1d35",
        "J": "\u1d36",
        "K": "\u1d37",
        "L": "\u1d38",
        "M": "\u1d39",
        "N": "\u1d3a",
        "O": "\u1d3c",
        "P": "\u1d3e",
        "R": "\u1d3f",
        "T": "\u1d40",
        "U": "\u1d41",
        "V": "\u2c7d",
        "W": "\u1d42",
        "+": "\u207a",
        "-": "\u207b",
        "=": "\u207c",
        "(": "\u207d",
        ")": "\u207e",
    }

    async def convert_word_async(self, word: str) -> str:
        """
        Convert a single word into the target format supported by the converter.

        Args:
            word (str): The word to be converted.

        Returns:
            str: The converted word.
        """
        result = []
        for char in word:
            if char in self._superscript_map:
                result.append(self._superscript_map[char])
            else:
                result.append(char)
        return "".join(result)
