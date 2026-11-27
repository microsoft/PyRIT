# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

from pyrit.converter._repeating_key_cipher import RepeatingKeyCipherConverter
from pyrit.models import ComponentIdentifier


class GronsfeldConverter(RepeatingKeyCipherConverter):
    """
    Encodes text using the Gronsfeld cipher with a specified numeric key.

    The Gronsfeld cipher is a variant of the Vigenere cipher that uses a sequence of digits (0-9)
    as the key instead of a keyword. Each letter of the plaintext is shifted by the amount given by
    the corresponding digit of the repeating numeric key. Because the key digits are limited to 0-9,
    the Gronsfeld cipher only ever uses the first ten of the twenty-six Vigenere alphabets.

    Using ``key="123"``, 'Hello' would encode to 'Igomq', as each letter is shifted by the amount
    corresponding to the aligned digit of the repeating key (1, 2, 3, 1, 2).
    Only alphabetic characters advance the position in the key; non-alphabetic characters
    (spaces, digits, punctuation) are passed through unchanged and do not consume a key digit.

    The repeating-key loop lives in the shared ``RepeatingKeyCipherConverter`` base; this class only
    supplies the numeric-key interpretation and the (Vigenere-style) shift rule.

    References:
        - Gronsfeld cipher / Vigenere cipher: https://en.wikipedia.org/wiki/Vigen%C3%A8re_cipher
        - "GPT-4 Is Too Smart To Be Safe: Stealthy Chat with LLMs via Cipher" (CipherChat),
          Yuan et al., arXiv:2308.06463: https://arxiv.org/abs/2308.06463

    Both the cipher and the referenced jailbreak framing are in the public domain / openly published.
    """

    _CIPHER_NAME = "gronsfeld"

    def __init__(self, *, key: "str | list[int]" = "31415") -> None:
        """
        Initialize the converter with a Gronsfeld numeric key.

        Args:
            key (str | list[int]): The numeric key used to encode the prompt. Either a string of
                digits (e.g. ``"31415"``) or a list of integers each in the range 0-9
                (e.g. ``[3, 1, 4, 1, 5]``). Defaults to ``"31415"``.

        Raises:
            ValueError: If ``key`` is empty, or contains any value that is not a single digit 0-9.
        """
        # Gronsfeld accepts a wider key type than the base (digits, as a string or list of ints),
        # so it builds the key stream directly rather than routing a list through the base
        # ``__init__(key: str)``. Preserve the original key as a digit string for the identifier.
        self.key = key if isinstance(key, str) else "".join(str(digit) for digit in key)
        self._key_stream = self._normalize_key(key)

    def _normalize_key(self, key: "str | list[int]") -> list[int]:
        """
        Validate the key and convert it to a list of integer shifts in the range 0-9.

        Args:
            key (str | list[int]): The numeric key, as a digit string or a list of ints.

        Returns:
            list[int]: The per-position shift amounts.

        Raises:
            ValueError: If ``key`` is empty or contains a value outside 0-9.
        """
        error = "gronsfeld key value invalid, must be a non-empty sequence of digits 0-9."
        if isinstance(key, str):
            if not key or not all(char in string.digits for char in key):
                raise ValueError(error)
            return [int(char) for char in key]

        shifts: list[int] = []
        for digit in key:
            # bool is a subclass of int; reject it explicitly to avoid silently treating True/False as 1/0.
            if isinstance(digit, bool) or not isinstance(digit, int) or not 0 <= digit <= 9:
                raise ValueError(error)
            shifts.append(digit)
        if not shifts:
            raise ValueError(error)
        return shifts

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Gronsfeld cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "key": self.key,
            },
        )

    def _encipher(self, letter_value: int, *, key_element: int) -> int:
        """
        Apply the Gronsfeld shift ``C = (P + digit) mod 26``.

        Args:
            letter_value (int): The plaintext letter value (A=0..Z=25).
            key_element (int): The aligned key digit (0-9).

        Returns:
            int: The ciphertext letter value (reduced modulo 26 by the base class).
        """
        return letter_value + key_element
