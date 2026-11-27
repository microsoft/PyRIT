# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import math
import string

from pyrit.converter._cipher_description import CIPHER_EXAMPLE, maybe_render_description
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class AffineConverter(Converter):
    """
    Encodes text using the classical Affine cipher.

    Each letter is mapped to an integer (``A``/``a`` = 0 ... ``Z``/``z`` = 25) and
    enciphered with ``E(x) = (a * x + b) mod 26``. The multiplier ``a`` must be coprime
    to 26 so the mapping is invertible; ``b`` is the additive shift. Letter case is
    preserved and non-alphabetic characters (digits, punctuation, whitespace) pass
    through unchanged.

    The Affine cipher generalizes the Caesar cipher (``a=1``) and the Atbash cipher
    (``a=25``, ``b=25``). With ``a=5`` and ``b=8``, ``"AFFINECIPHER"`` encodes to
    ``"IHHWVCSWFRCP"``.

    Source: Affine cipher, public domain
    (https://en.wikipedia.org/wiki/Affine_cipher). The jailbreak framing of
    communicating with a model purely through a classical cipher follows CipherChat
    (Yuan et al., "GPT-4 Is Too Smart To Be Safe", arXiv:2308.06463).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    # Multipliers coprime to 26 (the only valid values for ``a``).
    VALID_MULTIPLIERS = (1, 3, 5, 7, 9, 11, 15, 17, 19, 21, 23, 25)

    def __init__(self, *, a: int = 5, b: int = 8, append_description: bool = False) -> None:
        """
        Initialize the converter with the Affine cipher parameters.

        Args:
            a (int): The multiplicative key. Must be coprime to 26 (i.e. one of
                1, 3, 5, 7, 9, 11, 15, 17, 19, 21, 23, 25). Defaults to 5.
            b (int): The additive key (shift), taken modulo 26. Defaults to 8.
            append_description (bool): If True, appends plaintext "expert" text to the prompt.
                This includes instructions to only communicate using the cipher,
                a description of the cipher, and an example encoded using the cipher.

        Raises:
            ValueError: If ``a`` is not coprime to 26.
        """
        if math.gcd(a, 26) != 1:
            raise ValueError(
                f"Affine multiplier 'a'={a} is invalid; it must be coprime to 26 (one of {self.VALID_MULTIPLIERS})."
            )
        self.a = a
        self.b = b % 26
        self.append_description = append_description

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Affine cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "a": self.a,
                "b": self.b,
                "append_description": self.append_description,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the Affine cipher.

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
            self._affine(prompt),
            append_description=self.append_description,
            yaml_stem="affine",
            example_encoded=self._affine(CIPHER_EXAMPLE),
            a=str(self.a),
            b=str(self.b),
        )
        return ConverterResult(output_text=output_text, output_type="text")

    def _affine(self, text: str) -> str:
        def encipher(alphabet: str) -> str:
            return "".join(alphabet[(self.a * i + self.b) % 26] for i in range(len(alphabet)))

        alphabet = (string.ascii_lowercase, string.ascii_uppercase)
        enciphered_alphabet = tuple(map(encipher, alphabet))
        translation_table = str.maketrans("".join(alphabet), "".join(enciphered_alphabet))
        return text.translate(translation_table)
