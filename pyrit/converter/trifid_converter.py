# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

from pyrit.converter._polybius_square import FULL_ALPHABET, build_keyed_alphabet
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class TrifidConverter(Converter):
    """
    Encodes text using Delastelle's Trifid cipher.

    The Trifid cipher (invented by Felix Delastelle around 1902) is a classical fractionating
    transposition cipher. It extends the Bifid cipher from a 5x5 square to a 27-symbol alphabet
    arranged in a 3x3x3 cube: the 26 letters plus one extra "filler" symbol (classically ``+`` or
    ``.``). Every symbol is identified by three coordinates ``(layer, row, column)``, each in the
    range 1-3.

    Encryption works in groups of ``period`` letters:

    1. Each letter is replaced by its three coordinates, written as three stacked rows of digits.
    2. Within each group the digits are read off horizontally -- first every ``layer`` digit, then
       every ``row`` digit, then every ``column`` digit -- producing ``3 * group_length`` digits.
    3. The digit stream is re-split into consecutive triples, and each triple is mapped back through
       the cube to a ciphertext letter.

    Because coordinates are mixed across neighbouring letters, a single plaintext letter influences
    several ciphertext letters (diffusion), which is what distinguishes a fractionating cipher from a
    simple substitution.

    Using the historical key phrase ``"FELIX MARIE DELASTELLE"`` (cube alphabet
    ``FELIXMARDSTBCGHJKNOPQUVWYZ+``) with ``period=5``, the plaintext ``aide toi le ciel t'aidera``
    encodes to ``FMJFVOISSUFTFPUFEQQC``.

    Non-alphabet characters: input is upper-cased and every character that is not part of the cube
    alphabet (i.e. not an ASCII letter and not the filler symbol) is dropped before encoding, since
    the cipher is only defined over its 27-symbol alphabet. Spaces, digits and punctuation are
    therefore removed and the output is a single uppercase run of cipher letters.

    This converter is primarily intended for jailbreak-style red-teaming experiments that probe
    whether a model will decode a ciphered harmful request, following the "CipherChat" methodology
    (Yuan et al., "GPT-4 Is Too Smart To Be Safe: Stealthy Chat with LLMs via Cipher",
    arXiv:2308.06463).

    References:
        - Trifid cipher, Wikipedia: https://en.wikipedia.org/wiki/Trifid_cipher (public domain cipher).
        - CipherChat: arXiv:2308.06463.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, key: str = "", filler: str = "+", period: int = 5) -> None:
        """
        Initialize the Trifid converter.

        Args:
            key (str): Optional keyword used to build the mixed cube alphabet. Its letters (upper-cased,
                de-duplicated, non-letters ignored) are placed first, followed by the remaining letters
                of the alphabet in order, and finally the ``filler`` symbol -- giving the 27 cube symbols.
                An empty key (the default) yields the straight ``ABC...XYZ`` ordering plus the filler.
            filler (str): The 27th cube symbol. Must be a single character that is not an ASCII letter
                (so it cannot collide with a letter in the cube). Defaults to ``"+"``.
            period (int): The grouping size used for fractionation. Must be a positive integer.
                Defaults to the classic value of ``5``.

        Raises:
            ValueError: If ``filler`` is not a single non-alphabetic character, if ``key`` contains a
                character equal to ``filler``, or if ``period`` is less than 1.
        """
        if len(filler) != 1 or filler in string.ascii_letters:
            raise ValueError("trifid filler must be a single non-alphabetic character.")
        if filler in key:
            raise ValueError("trifid key must not contain the filler character.")
        if period < 1:
            raise ValueError("trifid period must be a positive integer.")

        self._key = key
        self._filler = filler
        self._period = period

        # Build the 27-symbol cube ordering via the shared helper: keyed letters, remaining
        # letters, then the filler as the 27th symbol. The 3-D coordinate lookup below is bespoke
        # (``PolybiusSquare`` only models the 2-D square ciphers).
        self._alphabet = "".join(build_keyed_alphabet(key, alphabet=FULL_ALPHABET, append=filler))

        # Map each symbol to its 1-based (layer, row, column) coordinates and back.
        self._symbol_to_coords: dict[str, tuple[int, int, int]] = {}
        self._coords_to_symbol: dict[tuple[int, int, int], str] = {}
        for index, symbol in enumerate(self._alphabet):
            coords = (index // 9 + 1, (index % 9) // 3 + 1, index % 3 + 1)
            self._symbol_to_coords[symbol] = coords
            self._coords_to_symbol[coords] = symbol

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the Trifid cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "key": self._key,
                "filler": self._filler,
                "period": self._period,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the Trifid cipher.

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

        return ConverterResult(output_text=self._trifid_encode(prompt), output_type="text")

    def _trifid_encode(self, text: str) -> str:
        """
        Apply the Trifid encryption to ``text``.

        Characters outside the cube alphabet are removed after upper-casing; the remaining symbols
        are fractionated group-by-group (groups of ``period``; the final group may be shorter).

        Args:
            text (str): The plaintext to encode.

        Returns:
            str: The encoded uppercase cipher text.
        """
        cleaned = [char for char in text.upper() if char in self._symbol_to_coords]

        result: list[str] = []
        for start in range(0, len(cleaned), self._period):
            group = cleaned[start : start + self._period]

            # Collect the three coordinate streams for the group.
            layers: list[int] = []
            rows: list[int] = []
            columns: list[int] = []
            for symbol in group:
                layer, row, column = self._symbol_to_coords[symbol]
                layers.append(layer)
                rows.append(row)
                columns.append(column)

            # Read the digits off horizontally, then re-split into triples.
            digits = layers + rows + columns
            for offset in range(0, len(digits), 3):
                triple = (digits[offset], digits[offset + 1], digits[offset + 2])
                result.append(self._coords_to_symbol[triple])

        return "".join(result)
