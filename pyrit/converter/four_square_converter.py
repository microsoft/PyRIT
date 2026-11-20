# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

from pyrit.converter._polybius_square import PolybiusSquare, build_keyed_alphabet, sanitize_to_alphabet
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class FourSquareConverter(Converter):
    """
    Encodes text using the Four-square cipher with two configurable keywords.

    The Four-square cipher (Felix Delastelle, c. 1902) is a manual symmetric digraph
    substitution cipher. It uses four 5x5 matrices arranged in a square:

        upper-left (plaintext)    upper-right (ciphertext, keyed with ``top_right_key``)
        lower-left  (ciphertext, keyed with ``bottom_left_key``)    lower-right (plaintext)

    Both plaintext squares hold the 25-letter alphabet in natural order; the two
    ciphertext squares are each built from a keyword (duplicate letters dropped) followed
    by the remaining letters of the alphabet in order. Because 26 letters do not fit in a
    5x5 grid, one letter is merged into another (by default ``"J"`` is merged into
    ``"I"``), which is the standard Playfair-family convention.

    Each plaintext digraph ``(a, b)`` is enciphered by the rectangle rule across the four
    squares: ``a`` is located in the upper-left plaintext square and ``b`` in the
    lower-right plaintext square; the first ciphertext letter is read from the upper-right
    square at ``(row of a, column of b)`` and the second from the lower-left square at
    ``(row of b, column of a)``. Unlike Playfair, Four-square needs no special handling of
    repeated letters within a digraph, so only a single trailing pad is ever required (for
    odd-length input).

    Non-alphabetic characters (spaces, digits, punctuation) and non-ASCII letters (e.g.
    accented characters) are **removed** before enciphering, because the cipher operates on
    a stream of letter digraphs; the output is uppercase ciphertext. If the cleaned letter
    stream has odd length, a single padding character (default ``"X"``) is appended to
    complete the final digraph.

    This is a public-domain classical cipher. Reference: the Wikipedia "Four-square cipher"
    article (https://en.wikipedia.org/wiki/Four-square_cipher). Encoding plaintext with a
    classical cipher to elicit unsafe model behavior is the jailbreak technique described in
    CipherChat ("GPT-4 Is Too Smart To Be Safe", arXiv:2308.06463).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        top_right_key: str,
        bottom_left_key: str,
        merge_letter: str = "J",
        merge_target: str = "I",
        padding_char: str = "X",
    ) -> None:
        """
        Initialize the converter with two Four-square keywords and letter-handling options.

        Args:
            top_right_key (str): Keyword for the upper-right ciphertext square (encodes the first
                letter of each digraph). Alphabetic characters only.
            bottom_left_key (str): Keyword for the lower-left ciphertext square (encodes the second
                letter of each digraph). Alphabetic characters only.
            merge_letter (str): The single letter dropped from the 5x5 alphabet (26 -> 25 letters).
                Any occurrence of this letter in the input (or keys) is replaced by ``merge_target``.
                Defaults to ``"J"``.
            merge_target (str): The single letter that ``merge_letter`` maps to. Must differ from
                ``merge_letter``. Defaults to ``"I"`` (i.e. J -> I, the standard convention).
            padding_char (str): Single letter appended to complete a trailing odd digraph.
                Must not be the merged-away letter. Defaults to ``"X"``.

        Raises:
            ValueError: If a key is empty or non-alphabetic, if ``merge_letter``/``merge_target``/
                ``padding_char`` is not a single ASCII letter, if ``merge_letter == merge_target``,
                or if ``padding_char`` equals the merged-away letter.
        """
        for name, key in (("top_right_key", top_right_key), ("bottom_left_key", bottom_left_key)):
            if not key or not all(char in string.ascii_letters for char in key):
                raise ValueError(f"Four-square {name} invalid, must be a non-empty alphabetic string.")

        for name, value in (
            ("merge_letter", merge_letter),
            ("merge_target", merge_target),
            ("padding_char", padding_char),
        ):
            if len(value) != 1 or value not in string.ascii_letters:
                raise ValueError(f"Four-square {name} must be a single ASCII letter.")

        self.merge_letter = merge_letter.upper()
        self.merge_target = merge_target.upper()
        if self.merge_letter == self.merge_target:
            raise ValueError("Four-square merge_letter and merge_target must differ.")

        self.padding_char = padding_char.upper()
        if self.padding_char == self.merge_letter:
            raise ValueError("Four-square padding_char cannot be the merged-away letter.")

        self.top_right_key = top_right_key
        self.bottom_left_key = bottom_left_key

        # The 25-letter alphabet with the merged-away letter removed.
        self._alphabet = "".join(c for c in string.ascii_uppercase if c != self.merge_letter)
        self._merge: tuple[str, str] = (self.merge_letter, self.merge_target)

        # Plaintext squares (upper-left and lower-right are identical): natural order.
        self._plain = PolybiusSquare(list(self._alphabet))

        # Ciphertext squares, keyed. The shared helper owns keyword de-duplication and the merge fold.
        self._cipher1 = PolybiusSquare(build_keyed_alphabet(top_right_key, alphabet=self._alphabet, merge=self._merge))
        self._cipher2 = PolybiusSquare(
            build_keyed_alphabet(bottom_left_key, alphabet=self._alphabet, merge=self._merge)
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Four-square cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "top_right_key": self.top_right_key,
                "bottom_left_key": self.bottom_left_key,
                "merge_letter": self.merge_letter,
                "merge_target": self.merge_target,
                "padding_char": self.padding_char,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the Four-square cipher.

        Args:
            prompt (str): The input prompt to be converted.
            input_type (PromptDataType): The type of the input prompt. Must be "text".

        Returns:
            ConverterResult: The result containing the uppercase ciphertext and its type.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        return ConverterResult(output_text=self._four_square(prompt), output_type="text")

    def _four_square(self, text: str) -> str:
        # Keep only ASCII letters, uppercase them, and apply the merge substitution.
        letters = sanitize_to_alphabet(text, alphabet_set=set(self._alphabet), merge=self._merge)

        # Pad an odd-length stream so it splits cleanly into digraphs.
        if len(letters) % 2 == 1:
            letters.append(self.padding_char)

        result: list[str] = []
        for i in range(0, len(letters), 2):
            row1, col1 = self._plain.position[letters[i]]
            row2, col2 = self._plain.position[letters[i + 1]]
            # First ciphertext letter: upper-right square at (row of first, col of second).
            result.append(self._cipher1.grid[row1][col2])
            # Second ciphertext letter: lower-left square at (row of second, col of first).
            result.append(self._cipher2.grid[row2][col1])

        return "".join(result)
