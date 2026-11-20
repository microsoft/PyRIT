# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

from pyrit.converter._polybius_square import (
    FULL_ALPHABET,
    PolybiusSquare,
    build_keyed_alphabet,
    sanitize_to_alphabet,
)
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class TwoSquareConverter(Converter):
    """
    Encodes text using the Two-square (a.k.a. double Playfair) digraph cipher.

    The Two-square cipher is a manual symmetric digraph substitution cipher built from two
    keyed 5x5 squares. The plaintext is broken into pairs of letters (digraphs); each pair is
    enciphered by looking the first letter up in one square and the second in the other, then
    reading off the two letters at the opposite corners of the rectangle they form.

    This implementation uses the **vertical** variant: the two squares are stacked one above the
    other. The first letter of each digraph is located in the top square (``key1``) and the second
    in the bottom square (``key2``). Given the first letter at ``(r1, c1)`` in the top square and the
    second at ``(r2, c2)`` in the bottom square, the ciphertext is ``top[r1][c2]`` followed by
    ``bottom[r2][c1]``. When both plaintext letters share a column (``c1 == c2``) the digraph maps to
    itself, exactly as in the classical description. The vertical Two-square cipher is an involution:
    applying this converter twice returns the original (sanitized) text.

    Because a 5x5 square holds only 25 cells, one letter of the alphabet is merged into another.
    By convention ``J`` is merged into ``I`` (both ``merge_letter`` and ``merge_target`` are
    configurable), so every occurrence of ``J`` in the key or plaintext is treated as ``I``.
    Non-letter characters (spaces, digits, punctuation) and any non-ASCII characters are removed
    before enciphering; the output is uppercase letters only. Unlike Playfair, repeated letters in a
    digraph need no special treatment, so no filler is inserted between them. If the sanitized text
    has an odd number of letters, a single ``padding_char`` (default ``X``) is appended to complete
    the final digraph.

    Worked example (Wikipedia, vertical variant, omitting ``Q`` instead of merging ``I``/``J``):
    ``key1="example"``, ``key2="keyword"``, ``merge_letter="Q"``. The plaintext
    ``"help me obiwan kenobi"`` enciphers to ``"HEDLXWSDJYANHOTKDG"``.

    References:
        - Two-square cipher. Wikipedia. https://en.wikipedia.org/wiki/Two-square_cipher
          (the cipher is in the public domain).
        - Yuan et al., "GPT-4 Is Too Smart To Be Safe: Stealthy Chat with LLMs via Cipher"
          (CipherChat), arXiv:2308.06463 — framing classical ciphers as a jailbreak channel.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        key1: str = "example",
        key2: str = "keyword",
        merge_letter: str = "J",
        merge_target: str = "I",
        padding_char: str = "X",
    ) -> None:
        """
        Initialize the converter with two keys and letter-handling options.

        Args:
            key1 (str): Keyword used to build the top 5x5 square. Alphabetic characters only.
            key2 (str): Keyword used to build the bottom 5x5 square. Alphabetic characters only.
            merge_letter (str): The single letter that is dropped from the 25-cell alphabet and
                treated as ``merge_target`` wherever it appears. Defaults to ``"J"``.
            merge_target (str): The single letter that ``merge_letter`` collapses into. Must differ
                from ``merge_letter`` and must itself survive in the reduced alphabet. Defaults to ``"I"``.
            padding_char (str): The single letter appended when the sanitized plaintext has an odd
                length. Must be part of the reduced alphabet. Defaults to ``"X"``.

        Raises:
            ValueError: If a key is empty or non-alphabetic, if ``merge_letter``/``merge_target``/
                ``padding_char`` are not single letters, if ``merge_letter == merge_target``, or if
                ``merge_target``/``padding_char`` is not present in the reduced alphabet.
        """
        self.merge_letter = self._validate_single_letter(merge_letter, name="merge_letter")
        self.merge_target = self._validate_single_letter(merge_target, name="merge_target")
        if self.merge_letter == self.merge_target:
            raise ValueError("merge_letter and merge_target must be different letters.")

        # The reduced 25-letter alphabet: the full ASCII uppercase alphabet minus the merged letter.
        self._alphabet = "".join(c for c in FULL_ALPHABET if c != self.merge_letter)
        self._alphabet_set = set(self._alphabet)
        self._merge = (self.merge_letter, self.merge_target)

        if self.merge_target not in self._alphabet_set:
            raise ValueError("merge_target must be a letter that remains in the reduced alphabet.")

        self.padding_char = self._validate_single_letter(padding_char, name="padding_char")
        if self.padding_char not in self._alphabet_set:
            raise ValueError("padding_char must be a letter that remains in the reduced alphabet.")

        self.key1 = self._validate_key(key1, name="key1")
        self.key2 = self._validate_key(key2, name="key2")

        # The vertical variant stacks two keyed squares: the first digraph letter is looked up in
        # the top square (key1), the second in the bottom square (key2).
        self._top = PolybiusSquare(build_keyed_alphabet(self.key1, alphabet=self._alphabet, merge=self._merge))
        self._bottom = PolybiusSquare(build_keyed_alphabet(self.key2, alphabet=self._alphabet, merge=self._merge))

    @staticmethod
    def _validate_single_letter(value: str, *, name: str) -> str:
        if not isinstance(value, str) or len(value) != 1 or value not in string.ascii_letters:
            raise ValueError(f"{name} must be a single ASCII letter.")
        return value.upper()

    def _validate_key(self, key: str, *, name: str) -> str:
        if not key or not all(char in string.ascii_letters for char in key):
            raise ValueError(f"{name} value invalid, must be a non-empty alphabetic string.")
        return key

    def _two_square(self, text: str) -> str:
        letters = sanitize_to_alphabet(text, alphabet_set=self._alphabet_set, merge=self._merge)
        if len(letters) % 2 == 1:
            letters.append(self.padding_char)

        result: list[str] = []
        for i in range(0, len(letters), 2):
            r1, c1 = self._top.position[letters[i]]
            r2, c2 = self._bottom.position[letters[i + 1]]
            # Vertical rule: ct1 = top[r1][c2], ct2 = bottom[r2][c1]; equal columns map to self.
            result.append(self._top.grid[r1][c2])
            result.append(self._bottom.grid[r2][c1])
        return "".join(result)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the Two-square cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "key1": self.key1,
                "key2": self.key2,
                "merge_letter": self.merge_letter,
                "merge_target": self.merge_target,
                "padding_char": self.padding_char,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the Two-square cipher.

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

        return ConverterResult(output_text=self._two_square(prompt), output_type="text")
