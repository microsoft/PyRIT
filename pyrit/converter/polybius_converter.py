# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter.word_level_converter import WordLevelConverter

_SQUARE = "ABCDEFGHIKLMNOPQRSTUVWXYZ"  # 25 letters, I/J merged (no J)


class PolybiusConverter(WordLevelConverter):
    """
    Encodes text using the Polybius square.

    The Polybius square is a classical cipher that maps each letter of the alphabet to a
    pair of coordinates (row, column) on a 5x5 grid. Because the Latin alphabet has 26
    letters but the grid holds only 25 cells, ``I`` and ``J`` are merged into a single cell
    (any ``J`` is treated as ``I``). The standard square is::

           1 2 3 4 5
        1  A B C D E
        2  F G H I K      (J shares I's cell)
        3  L M N O P
        4  Q R S T U
        5  V W X Y Z

    With this square ``A`` encodes to ``11``, ``B`` to ``12``, ``I`` (and ``J``) to ``24``,
    and ``Z`` to ``55``. Each ASCII letter is replaced by its two-digit coordinate pair and the
    pairs within a word are separated by a single space. A non-letter character (digit,
    punctuation, non-ASCII letter) is emitted as its own space-separated token, and word
    boundaries are written as ``/`` so that the words and any literal digits stay distinct from
    the coordinate pairs (``"AB CD"`` gives ``11 12 / 13 14`` while ``"ABCD"`` gives
    ``11 12 13 14``). Encoding is case-insensitive.

    Reference: https://en.wikipedia.org/wiki/Polybius_square (public domain cipher).

    Like other classical cipher converters, this is useful for probing whether a model will
    decode and act on obfuscated content, following the cipher-based jailbreak framing of
    CipherChat ("GPT-4 Is Too Smart To Be Safe", Yuan et al., arXiv:2308.06463).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    _WORD_SEPARATOR = "/"

    # Each letter -> its two-digit (row, column) coordinate on the 5x5 square; J shares I's cell.
    _ENCODING = {letter: f"{index // 5 + 1}{index % 5 + 1}" for index, letter in enumerate(_SQUARE)}
    _ENCODING["J"] = _ENCODING["I"]

    async def convert_word_async(self, word: str) -> str:
        """
        Convert one word into Polybius square coordinates.

        Each ASCII letter becomes its two-digit coordinate pair; every other character (digit,
        punctuation, non-ASCII letter) passes through unchanged. Tokens are separated by a single
        space so that letters, literal digits, and coordinate pairs stay distinguishable.

        Args:
            word (str): The word to convert.

        Returns:
            str: The converted word, with per-character tokens separated by spaces.
        """
        output = [self._ENCODING.get(char.upper(), char) if char.isascii() else char for char in word]
        return " ".join(output)

    def join_words(self, words: list[str]) -> str:
        """
        Join converted words while preserving every source-space boundary.

        Args:
            words (list[str]): The converted words.

        Returns:
            str: The converted words separated by explicit ``/`` word tokens.
        """
        output: list[str] = []
        for index, word in enumerate(words):
            if index:
                output.append(self._WORD_SEPARATOR)
            if word:
                output.append(word)

        return " ".join(output)

    @classmethod
    def to_ascii_equivalent(cls, *, text: str) -> str | None:
        """
        Return the text a target reconstructs when it decodes the square correctly.

        Because ``J`` shares ``I``'s cell, a correctly decoded prompt answers with ``I`` for each
        ``J``. The Encoding scenario feeds this value to ``DecodingScorer`` as ``decoded_text`` so a
        seed containing ``J`` can still score true.

        Args:
            text (str): The original (pre-encoding) seed text.

        Returns:
            str | None: ``text`` with ``J``/``j`` folded to ``I``/``i``, or ``None`` when the text
                contains no ``J`` and nothing changes.
        """
        folded = text.replace("J", "I").replace("j", "i")
        return folded if folded != text else None
