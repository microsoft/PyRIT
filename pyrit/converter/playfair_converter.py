# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

from pyrit.converter._cipher_description import CIPHER_EXAMPLE, maybe_render_description
from pyrit.converter._polybius_square import PolybiusSquare, build_keyed_alphabet
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class PlayfairConverter(Converter):
    """
    Encodes text using the classical Playfair cipher with a configurable key.

    The Playfair cipher is a manual symmetric digraph-substitution cipher invented by Charles
    Wheatstone in 1854 and promoted by Lord Playfair. A keyword fills a 5x5 square with the
    letters of the alphabet (one letter is merged with another so 25 letters fit, conventionally
    I/J). The plaintext is split into letter pairs and each pair is enciphered by one of three
    rules depending on whether the two letters share a row, share a column, or form a rectangle in
    the square. See the public-domain description at https://en.wikipedia.org/wiki/Playfair_cipher.

    Because the cipher operates on pairs of distinct letters, the plaintext is first reduced to
    letters only (non-alphabetic characters are dropped) and a filler letter is inserted to split
    doubled letters and to pad an odd-length message; the merged letter (``j`` by default) is
    replaced by its partner (``i``). As a result the transformation is not perfectly reversible,
    which is typical of hand ciphers.

    Classical ciphers such as Playfair have been used to probe whether safety-aligned LLMs will
    decode and act on content that bypasses their alignment, as studied in CipherChat
    ("GPT-4 Is Too Smart To Be Safe", Yuan et al., arXiv:2308.06463).

    Using ``key="playfair example"`` with the default I/J merge and ``X`` filler, the message
    'Hide the gold in the tree stump' encodes to 'BMODZBXDNABEKUDMUIXMMOUVIF'.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        key: str,
        merge_letter: str = "j",
        merge_target: str = "i",
        padding_char: str = "x",
        append_description: bool = False,
    ) -> None:
        """
        Initialize the converter with a Playfair key square and encoding options.

        Args:
            key (str): The key phrase used to build the 5x5 key square. Must contain at least one
                ASCII letter; non-letter characters (such as spaces) are ignored.
            merge_letter (str): The single letter that is omitted from the square and folded into
                ``merge_target`` (classically ``"j"``). Case-insensitive.
            merge_target (str): The single letter that ``merge_letter`` is replaced with; it is the
                letter kept in the square (classically ``"i"``). Case-insensitive.
            padding_char (str): The single filler letter inserted to break up doubled letters in a
                pair and to pad an odd-length message (classically ``"x"``). Case-insensitive.
            append_description (bool): If True, prepends a plaintext description of the cipher and a
                worked example to the encoded prompt.

        Raises:
            ValueError: If ``key`` contains no ASCII letters; if ``merge_letter``,
                ``merge_target`` or ``padding_char`` is not a single ASCII letter; if
                ``merge_letter`` and ``merge_target`` are the same letter; or if ``merge_target``
                or ``padding_char`` is the merged-away letter.
        """
        if not any(char in string.ascii_letters for char in key):
            raise ValueError("playfair key value invalid, must contain at least one letter.")

        merge_letter = self._validate_single_letter(merge_letter, name="merge_letter")
        merge_target = self._validate_single_letter(merge_target, name="merge_target")
        padding_char = self._validate_single_letter(padding_char, name="padding_char")

        if merge_letter == merge_target:
            raise ValueError("playfair merge_letter and merge_target must be different letters.")
        if padding_char == merge_letter:
            raise ValueError("playfair padding_char cannot be the merged-away letter.")

        self.key = key
        self.merge_letter = merge_letter
        self.merge_target = merge_target
        self.padding_char = padding_char
        self.append_description = append_description

        # Build the keyed 5x5 square via the shared helper (which owns the keyword de-duplication
        # and the merge fold), then lower-case it to match Playfair's lowercase internals.
        upper_alphabet = "".join(c for c in string.ascii_uppercase if c != self.merge_letter.upper())
        letters = [
            char.lower()
            for char in build_keyed_alphabet(
                self.key, alphabet=upper_alphabet, merge=(self.merge_letter.upper(), self.merge_target.upper())
            )
        ]
        self._square = PolybiusSquare(letters)
        # A secondary filler used when the primary filler would itself need to be doubled.
        self._alt_padding = next(letter for letter in letters if letter != self.padding_char)

    @staticmethod
    def _validate_single_letter(value: str, *, name: str) -> str:
        """
        Validate that a parameter is a single ASCII letter and return it lowercased.

        Args:
            value (str): The provided parameter value.
            name (str): The parameter name, used in the error message.

        Returns:
            str: The validated letter, lowercased.

        Raises:
            ValueError: If ``value`` is not exactly one ASCII letter.
        """
        if len(value) != 1 or value not in string.ascii_letters:
            raise ValueError(f"playfair {name} must be a single alphabetic character.")
        return value.lower()

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Playfair cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "key": self.key,
                "merge_letter": self.merge_letter,
                "merge_target": self.merge_target,
                "padding_char": self.padding_char,
                "append_description": self.append_description,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the Playfair cipher.

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

        output_text = maybe_render_description(
            self._playfair(prompt),
            append_description=self.append_description,
            yaml_stem="playfair",
            example_encoded=self._playfair(CIPHER_EXAMPLE),
            key=self.key,
            merge_letter=self.merge_letter,
            merge_target=self.merge_target,
            padding_char=self.padding_char,
        )
        return ConverterResult(output_text=output_text, output_type="text")

    def _playfair(self, text: str) -> str:
        """
        Encode ``text`` with the Playfair cipher.

        Args:
            text (str): The plaintext to encode.

        Returns:
            str: The uppercase Playfair ciphertext (letters only).
        """
        letters = [char for char in text.lower() if char in string.ascii_lowercase]
        letters = [self.merge_target if char == self.merge_letter else char for char in letters]

        digraphs = self._to_digraphs(letters)

        result: list[str] = []
        for first, second in digraphs:
            result.append(self._encode_pair(first=first, second=second))
        return "".join(result).upper()

    def _filler_for(self, letter: str) -> str:
        """
        Return a filler letter distinct from ``letter``.

        Args:
            letter (str): The letter the filler must differ from.

        Returns:
            str: ``padding_char``, or the alternate filler when ``letter`` is ``padding_char``.
        """
        return self.padding_char if letter != self.padding_char else self._alt_padding

    def _to_digraphs(self, letters: list[str]) -> list[tuple[str, str]]:
        """
        Split a list of letters into Playfair digraphs, inserting fillers as needed.

        Doubled letters within a pair are separated by a filler, and an odd final letter is
        padded with a filler.

        Args:
            letters (list[str]): The cleaned plaintext letters.

        Returns:
            list[tuple[str, str]]: The list of letter pairs to encode.
        """
        digraphs: list[tuple[str, str]] = []
        index = 0
        while index < len(letters):
            first = letters[index]
            if index + 1 < len(letters):
                second = letters[index + 1]
                if first == second:
                    second = self._filler_for(first)
                    index += 1
                else:
                    index += 2
            else:
                second = self._filler_for(first)
                index += 1
            digraphs.append((first, second))
        return digraphs

    def _encode_pair(self, *, first: str, second: str) -> str:
        """
        Encode a single Playfair digraph using the row/column/rectangle rules.

        Args:
            first (str): The first letter of the pair.
            second (str): The second letter of the pair.

        Returns:
            str: The two-letter encoded digraph.
        """
        first_row, first_col = self._square.position[first]
        second_row, second_col = self._square.position[second]

        if first_row == second_row:
            # Same row: take the letter to the right of each (wrapping around).
            new_first = self._square.grid[first_row][(first_col + 1) % 5]
            new_second = self._square.grid[second_row][(second_col + 1) % 5]
        elif first_col == second_col:
            # Same column: take the letter below each (wrapping around).
            new_first = self._square.grid[(first_row + 1) % 5][first_col]
            new_second = self._square.grid[(second_row + 1) % 5][second_col]
        else:
            # Rectangle: swap columns, keep rows.
            new_first = self._square.grid[first_row][second_col]
            new_second = self._square.grid[second_row][first_col]

        return new_first + new_second
