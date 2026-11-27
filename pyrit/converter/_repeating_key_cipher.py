# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared base for repeating-key polyalphabetic substitution ciphers (the Vigenere family).

Vigenere, Beaufort, Gronsfeld and Porta are all built on the same idea: walk the input one
character at a time, and for every *alphabetic* character pull the next element from a repeating
key stream and map the letter through a per-cipher rule. Non-alphabetic characters pass through
unchanged and do not consume a key element, and the case of each input letter is preserved.

This module factors out that identical key-stream loop so each cipher only has to express:

- ``_normalize_key`` — how the constructor key becomes the integer key stream (letters A=0..Z=25
  by default; Gronsfeld overrides this to use digits 0-9).
- ``_encipher`` — the per-letter rule mapping ``(letter_value, key_element)`` to a ciphertext value.

This module is intentionally private (underscore prefix): it is an internal helper shared by the
cipher converters, not part of the public ``pyrit.converter`` API.
"""

import string
from abc import abstractmethod

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType

_ALPHABET_SIZE = 26


class RepeatingKeyCipherConverter(Converter):
    """
    Base class for repeating-key polyalphabetic substitution ciphers.

    Subclasses set ``_CIPHER_NAME`` (used in the key-validation error message) and implement
    ``_encipher``; they may override ``_normalize_key`` to change how the key is interpreted. The
    base owns the key-stream loop, case preservation and the pass-through of non-letters.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    #: Lower-case cipher name used in the default key-validation error message.
    _CIPHER_NAME: str = "cipher"

    def __init__(self, *, key: str) -> None:
        """
        Initialize the converter and build the integer key stream from ``key``.

        Args:
            key (str): The cipher key. Interpreted by ``_normalize_key`` (alphabetic keyword by
                default).

        Raises:
            ValueError: If ``_normalize_key`` rejects ``key``.
        """
        self.key = key
        self._key_stream = self._normalize_key(key)

    def _normalize_key(self, key: str) -> list[int]:
        """
        Validate an alphabetic keyword and return its letter values (A=0..Z=25).

        Args:
            key (str): The keyword, which must be a non-empty string of ASCII letters.

        Returns:
            list[int]: The per-position key values in the range 0-25.

        Raises:
            ValueError: If ``key`` is empty or contains a non-alphabetic character.
        """
        if not key or not all(char in string.ascii_letters for char in key):
            raise ValueError(f"{self._CIPHER_NAME} key value invalid, must be a non-empty alphabetic string.")
        return [ord(char) - ord("A") for char in key.upper()]

    @abstractmethod
    def _encipher(self, letter_value: int, *, key_element: int) -> int:
        """
        Map a plaintext letter to its ciphertext value under the aligned key element.

        Args:
            letter_value (int): The plaintext letter value (A=0..Z=25), case already stripped.
            key_element (int): The aligned element of the key stream.

        Returns:
            int: The ciphertext letter value; the caller reduces it modulo 26, so negative or
                out-of-range results are acceptable.
        """

    def _transform(self, text: str) -> str:
        """
        Apply the cipher to ``text`` via the shared repeating-key loop.

        Args:
            text (str): The input text.

        Returns:
            str: The enciphered text, with case preserved and non-letters passed through.
        """
        key_length = len(self._key_stream)
        key_index = 0
        result: list[str] = []
        for char in text:
            if char in string.ascii_letters:
                key_element = self._key_stream[key_index % key_length]
                base = ord("A") if char in string.ascii_uppercase else ord("a")
                value = self._encipher(ord(char) - base, key_element=key_element)
                result.append(chr(value % _ALPHABET_SIZE + base))
                key_index += 1
            else:
                result.append(char)
        return "".join(result)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the cipher.

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

        return ConverterResult(output_text=self._transform(prompt), output_type="text")
