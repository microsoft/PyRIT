# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class ColumnarTranspositionConverter(Converter):
    """
    Encodes text using the (incomplete-columns) columnar transposition cipher.

    The columnar transposition cipher is a classical transposition cipher: the plaintext is written
    row-wise into a grid whose number of columns equals the length of a keyword, and the ciphertext is
    produced by reading the columns out one at a time in the alphabetical order of the keyword's letters.
    It is a pure permutation of the input characters (no letters are substituted), so every character of
    the prompt - including spaces, digits, and punctuation - is placed in the grid and the transformation
    is fully reversible given the keyword and the message length.

    This implementation uses the standard *incomplete-columns* variant: the final row of the grid may be
    only partially filled, producing columns of unequal length, and **no padding characters are added**.
    Ties between repeated keyword letters are broken by their original left-to-right position (a stable
    sort), the usual convention.

    Using ``key="ZEBRAS"``, the classic worked example 'WEAREDISCOVEREDFLEEATONCE' encodes to
    'EVLNACDTESEAROFODEECWIREE'.

    References:
        - Wikipedia, "Transposition cipher" (columnar transposition section), public domain:
          https://en.wikipedia.org/wiki/Transposition_cipher
        - CipherChat [@yuan2023cipherchat], which frames classical ciphers as a jailbreak technique.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, key: str) -> None:
        """
        Initialize the converter with a columnar transposition keyword.

        Args:
            key (str): The keyword whose length sets the number of columns and whose alphabetical
                letter order determines the column read-out order. Must consist of alphabetic
                characters only. Ordering is case-insensitive; repeated letters keep their original
                left-to-right order.

        Raises:
            ValueError: If ``key`` is empty or contains non-alphabetic characters.
        """
        if not key or not all(char in string.ascii_letters for char in key):
            raise ValueError("columnar transposition key value invalid, must be a non-empty alphabetic string.")
        self.key = key

    @property
    def decode_prompt_description(self) -> str:
        """
        A human-readable description of this cipher, including its keyword, for use in decode prompts.

        A keyed transposition cannot be reversed without the keyword, so a scenario that asks a model
        to decode the output must disclose the key and the column-ordering convention; otherwise a
        failure to decode reflects the missing key rather than the behavior under test.

        Returns:
            str: A phrase naming the cipher, its keyword, and the incomplete-column read order.
        """
        return (
            f'columnar transposition cipher (keyword "{self.key.upper()}", written row-wise into a grid '
            "with that many columns, incomplete final row, then read out column by column in the "
            "alphabetical order of the keyword's letters)"
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with columnar transposition parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "key": self.key,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the columnar transposition cipher.

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

        return ConverterResult(output_text=self._columnar(prompt), output_type="text")

    def _columnar(self, text: str) -> str:
        num_columns = len(self.key)
        # Rank the columns by the alphabetical order of the (uppercased) key letters. Ties between
        # repeated letters are broken by original index, so the sort is stable left-to-right.
        key_upper = self.key.upper()
        column_order = sorted(range(num_columns), key=lambda i: (key_upper[i], i))

        # Write the text into the grid row-wise: character i lands in column (i % num_columns). The
        # final row may be incomplete, so columns can have unequal length; no padding is added.
        columns: list[list[str]] = [[] for _ in range(num_columns)]
        for index, char in enumerate(text):
            columns[index % num_columns].append(char)

        # Read the columns out in alphabetical key order and concatenate.
        return "".join("".join(columns[i]) for i in column_order)
