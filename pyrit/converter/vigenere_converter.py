# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter._cipher_description import CIPHER_EXAMPLE, maybe_render_description
from pyrit.converter._repeating_key_cipher import RepeatingKeyCipherConverter
from pyrit.converter.converter import ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class VigenereConverter(RepeatingKeyCipherConverter):
    """
    Encodes text using the Vigenere cipher with a specified key.

    The Vigenere cipher is a polyalphabetic substitution cipher that generalizes the Caesar cipher:
    instead of a single fixed shift, each letter of the plaintext is shifted by an amount determined by
    the corresponding letter of a repeating keyword.

    Using ``key="key"``, 'Hello' would encode to 'Rijvs', as each letter is shifted by the amount
    corresponding to the aligned letter of the repeating key ('k', 'e', 'y', 'k', 'e').
    Only alphabetic characters advance the position in the key; non-alphabetic characters
    (spaces, digits, punctuation) are passed through unchanged and do not consume a key letter.

    The repeating-key loop lives in the shared ``RepeatingKeyCipherConverter`` base; this class only
    supplies the Vigenere shift rule and the optional cipher-description framing.
    """

    _CIPHER_NAME = "vigenere"

    def __init__(self, *, key: str, append_description: bool = False) -> None:
        """
        Initialize the converter with a Vigenere cipher key and an option to append a description.

        Args:
            key (str): The keyword used to encode the prompt. Must consist of alphabetic characters only.
            append_description (bool): If True, appends plaintext "expert" text to the prompt.
                This includes instructions to only communicate using the cipher,
                a description of the cipher, and an example encoded using the cipher.

        Raises:
            ValueError: If ``key`` is empty or contains non-alphabetic characters.
        """
        super().__init__(key=key)
        self.append_description = append_description

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Vigenere cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "key": self.key,
                "append_description": self.append_description,
            },
        )

    def _encipher(self, letter_value: int, *, key_element: int) -> int:
        """
        Apply the Vigenere shift ``C = (P + K) mod 26``.

        Args:
            letter_value (int): The plaintext letter value (A=0..Z=25).
            key_element (int): The aligned key letter value (A=0..Z=25).

        Returns:
            int: The ciphertext letter value (reduced modulo 26 by the base class).
        """
        return letter_value + key_element

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the Vigenere cipher.

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
            self._transform(prompt),
            append_description=self.append_description,
            yaml_stem="vigenere",
            example_encoded=self._transform(CIPHER_EXAMPLE),
            key=self.key,
        )
        return ConverterResult(output_text=output_text, output_type="text")
