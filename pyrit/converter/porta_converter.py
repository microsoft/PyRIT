# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._repeating_key_cipher import RepeatingKeyCipherConverter
from pyrit.models import ComponentIdentifier


class PortaConverter(RepeatingKeyCipherConverter):
    """
    Encodes text using the Porta cipher with a specified key.

    The Porta cipher (Giovan Battista della Porta, 16th century) is a reciprocal polyalphabetic
    substitution cipher. The alphabet is split in half (A-M and N-Z), and each key letter selects one
    of 13 cipher alphabets from the classic Porta tableau. A letter in the first half (A-M) always maps
    to a letter in the second half (N-Z) and vice versa, which makes the cipher reciprocal: applying the
    same key a second time recovers the plaintext (``encrypt(encrypt(x)) == x``). As a notable special
    case, a key of "A"/"B" selects the first row and is equivalent to ROT13.

    As with the Vigenere cipher, the key repeats over the message; only alphabetic characters advance the
    key position, while non-alphabetic characters (spaces, digits, punctuation) and non-ASCII letters are
    passed through unchanged and do not consume a key letter. Letter case of the input is preserved, and
    the key is treated case-insensitively. The repeating-key loop lives in the shared
    ``RepeatingKeyCipherConverter`` base; this class only supplies the Porta combining rule.

    References:
        - Porta / della Porta cipher (classic tableau): https://en.wikipedia.org/wiki/Tabula_recta and
          https://en.wikipedia.org/wiki/Giovan_Battista_della_Porta
        - Classical-cipher jailbreak framing (CipherChat): https://arxiv.org/abs/2308.06463
    """

    _CIPHER_NAME = "porta"
    _HALF = 13  # Half of the 26-letter alphabet; Porta works on two halves of 13 letters each.

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Porta cipher parameters.

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
        Apply the reciprocal Porta rule using the key letter to select one of 13 tableau rows.

        Args:
            letter_value (int): The plaintext letter value (A=0..Z=25).
            key_element (int): The aligned key letter value (A=0..Z=25); the row is ``key_element // 2``.

        Returns:
            int: The ciphertext letter value (already in range 0-25).
        """
        # The row (0..12) is selected by the key letter: A/B -> 0, C/D -> 1, ..., Y/Z -> 12.
        row = key_element // 2
        if letter_value < self._HALF:
            # First half (A-M) maps into the second half (N-Z).
            return (letter_value + row) % self._HALF + self._HALF
        # Second half (N-Z) maps back into the first half (A-M).
        return (letter_value - self._HALF - row) % self._HALF
