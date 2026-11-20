# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class RailFenceConverter(Converter):
    """
    Encodes text using the Rail Fence transposition cipher with a configurable number of rails.

    The Rail Fence cipher writes the plaintext downward and diagonally across a number of
    successive "rails" of an imaginary fence, bouncing up and down between the top and bottom
    rails, then reads the ciphertext off row by row. For example, with ``rails=3`` the text
    'WEAREDISCOVEREDFLEEATONCE' is laid out as::

        W . . . E . . . C . . . R . . . L . . . T . . . E
        . E . R . D . S . O . E . E . F . E . A . O . C .
        . . A . . . I . . . V . . . D . . . E . . . N . .

    and read off by row to produce 'WECRLTEERDSOEEFEAOCAIVDEN'. Every character (including
    spaces, digits, and punctuation) participates in the zigzag, so the transformation is a
    pure permutation of the input and is fully reversible given the number of rails.

    References:
        Rail Fence cipher (public domain classical cipher):
        https://en.wikipedia.org/wiki/Rail_fence_cipher

        CipherChat jailbreak framing (encoding harmful prompts with classical ciphers to
        evade safety alignment): Yuan et al., "GPT-4 Is Too Smart To Be Safe: Stealthy Chat
        with LLMs via Cipher", arXiv:2308.06463, https://arxiv.org/abs/2308.06463
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, rails: int = 3) -> None:
        """
        Initialize the converter with a number of rails.

        Args:
            rails (int): The number of rails (rows) of the fence. Must be an integer greater
                than or equal to 2. Defaults to 3.

        Raises:
            ValueError: If ``rails`` is less than 2.
        """
        if rails < 2:
            raise ValueError("rail fence rails value invalid, must be an integer greater than or equal to 2.")
        self.rails = rails

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Rail Fence cipher parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "rails": self.rails,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the Rail Fence cipher.

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

        return ConverterResult(output_text=self._rail_fence(prompt), output_type="text")

    def _rail_fence(self, text: str) -> str:
        # A single rail would leave the text unchanged; rails >= 2 is enforced in __init__.
        rows: list[list[str]] = [[] for _ in range(self.rails)]
        rail = 0
        direction = 1
        for char in text:
            rows[rail].append(char)
            # Reverse direction at the top and bottom rails so the path zigzags between them.
            if rail == 0:
                direction = 1
            elif rail == self.rails - 1:
                direction = -1
            rail += direction
        return "".join("".join(row) for row in rows)
