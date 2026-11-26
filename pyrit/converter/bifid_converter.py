# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class BifidConverter(Converter):
    """
    Encodes text using the Bifid cipher.

    The Bifid cipher, invented by Felix Delastelle around 1901, is a classical fractionating
    transposition cipher. It combines a Polybius square with transposition to achieve
    diffusion: each plaintext letter is first replaced by its (row, column) coordinates in a
    5x5 Polybius square, the coordinates are then fractionated (all rows written out, followed
    by all columns), and finally re-paired into new coordinates that are mapped back to
    letters. Because the two coordinates of a single ciphertext letter are typically drawn from
    two different plaintext letters, a single-character change in the plaintext affects multiple
    ciphertext characters.

    The 5x5 Polybius square holds 25 cells, so one pair of letters must share a cell; by
    convention ``I`` and ``J`` are merged (any ``J`` in the plaintext is treated as ``I``). The
    square can be seeded with a keyword via the ``key`` argument: the (de-duplicated) keyword
    letters are placed first, followed by the remaining letters of the alphabet. With the
    default empty key the square is the plain alphabet ``ABCDEFGHIKLMNOPQRSTUVWXYZ``.

    Fractionation can be performed over the whole message at once (the default, ``period=0``) or
    in fixed-size blocks when a positive ``period`` is supplied, matching the classic
    period-based Bifid variant.

    Non-letter characters (spaces, digits, punctuation) and the ``I``/``J`` merge mean the
    transformation is not perfectly reversible; this converter uppercases the input, drops every
    character that is not an ASCII letter, and returns a contiguous block of uppercase
    ciphertext letters.

    References:
        - Wikipedia, "Bifid cipher": https://en.wikipedia.org/wiki/Bifid_cipher
        - CipherChat [@yuan2023cipherchat] — framing classical ciphers as a jailbreak
          vector for evaluating LLM safety.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    _ALPHABET = "ABCDEFGHIKLMNOPQRSTUVWXYZ"  # 25 letters, I/J merged (no J)

    def __init__(self, *, key: str = "", period: int = 0) -> None:
        """
        Initialize the converter with an optional keyword and period.

        Args:
            key (str): Keyword used to seed the 5x5 Polybius square. Non-letter characters are
                ignored and letters are de-duplicated (keeping first occurrence) before the rest
                of the alphabet is appended. ``J`` is treated as ``I``. Defaults to an empty
                string, which yields the plain-alphabet square ``ABCDEFGHIKLMNOPQRSTUVWXYZ``.
            period (int): Size of the fractionation block. ``0`` (the default) fractionates the
                entire message as a single block. A positive value splits the message into
                blocks of that many letters, fractionating each independently.

        Raises:
            ValueError: If ``period`` is negative.
        """
        if period < 0:
            raise ValueError("Bifid period must be a non-negative integer.")

        self.key = key
        self.period = period
        self._square = self._build_square(key)
        # Map each letter to its (row, col) coordinates and each coordinate pair back to a letter.
        self._letter_to_coords: dict[str, tuple[int, int]] = {}
        self._coords_to_letter: dict[tuple[int, int], str] = {}
        for index, letter in enumerate(self._square):
            row, col = divmod(index, 5)
            self._letter_to_coords[letter] = (row, col)
            self._coords_to_letter[(row, col)] = letter

    @classmethod
    def _build_square(cls, key: str) -> str:
        """
        Build the 25-letter Polybius square ordering from an optional keyword.

        Args:
            key (str): The keyword to seed the square. Non-letters are ignored, ``J`` folds to
                ``I``, and letters are de-duplicated keeping first occurrence.

        Returns:
            str: A 25-character string of unique uppercase letters (no ``J``).
        """
        seen: list[str] = []
        for char in key.upper():
            if char not in string.ascii_uppercase:
                continue
            letter = "I" if char == "J" else char
            if letter not in seen:
                seen.append(letter)
        for letter in cls._ALPHABET:
            if letter not in seen:
                seen.append(letter)
        return "".join(seen)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Bifid cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "key": self.key,
                "period": self.period,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the Bifid cipher.

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

        return ConverterResult(output_text=self._bifid(prompt), output_type="text")

    def _bifid(self, text: str) -> str:
        """
        Apply the Bifid encoding to ``text``.

        Letters are uppercased and ``J`` folded to ``I``; every non-letter is dropped. The
        remaining letters are fractionated in blocks of ``self.period`` (or as a single block
        when the period is 0) and re-paired into ciphertext letters.

        Returns:
            str: The Bifid-encoded ciphertext (uppercase letters only).
        """
        letters = ["I" if char == "J" else char for char in text.upper() if char in string.ascii_uppercase]
        if not letters:
            return ""

        block_size = self.period if self.period > 0 else len(letters)
        result_chars: list[str] = []
        for start in range(0, len(letters), block_size):
            block = letters[start : start + block_size]
            rows = [self._letter_to_coords[letter][0] for letter in block]
            cols = [self._letter_to_coords[letter][1] for letter in block]
            # Fractionate: concatenate all rows then all columns, then read off coordinate pairs.
            sequence = rows + cols
            result_chars.extend(
                self._coords_to_letter[(sequence[i], sequence[i + 1])] for i in range(0, len(sequence), 2)
            )

        return "".join(result_chars)
