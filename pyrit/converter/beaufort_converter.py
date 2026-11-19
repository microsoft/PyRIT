# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._repeating_key_cipher import RepeatingKeyCipherConverter
from pyrit.models import ComponentIdentifier


class BeaufortConverter(RepeatingKeyCipherConverter):
    """
    Encodes text using the Beaufort cipher with a specified key.

    The Beaufort cipher is a reciprocal polyalphabetic substitution cipher built on the same
    tabula recta as the Vigenere cipher, but with a different combining rule. Each plaintext
    letter is enciphered as ``C = (K - P) mod 26``, where ``P`` is the plaintext letter value
    (A=0..Z=25) and ``K`` is the value of the aligned letter of the repeating keyword. Because
    subtracting twice returns the original value (``P = (K - C) mod 26``), encryption and
    decryption are the identical operation: applying the converter twice with the same key
    restores the original text.

    Using ``key="key"``, 'Hello' would encode to 'Danzq', as each letter is combined with the
    aligned letter of the repeating key ('k', 'e', 'y', 'k', 'e') via the Beaufort rule. Only
    alphabetic characters advance the position in the key; non-alphabetic characters (spaces,
    digits, punctuation) are passed through unchanged and do not consume a key letter.

    The repeating-key loop (case handling, non-letter pass-through) lives in the shared
    ``RepeatingKeyCipherConverter`` base; this class only supplies the Beaufort combining rule.

    References:
        - Beaufort cipher: https://en.wikipedia.org/wiki/Beaufort_cipher (public domain).
        - CipherChat ("GPT-4 Is Too Smart To Be Safe: Stealthy Chat with LLMs via Cipher"),
          arXiv:2308.06463, which frames classical ciphers as a jailbreak vector by having a
          model communicate only through the cipher.
    """

    _CIPHER_NAME = "beaufort"

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Beaufort cipher parameters.

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
        Apply the Beaufort rule ``C = (K - P) mod 26`` (reciprocal — applying it twice restores P).

        Args:
            letter_value (int): The plaintext letter value (A=0..Z=25).
            key_element (int): The aligned key letter value (A=0..Z=25).

        Returns:
            int: The ciphertext letter value (reduced modulo 26 by the base class).
        """
        return key_element - letter_value
