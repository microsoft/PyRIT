# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class CharNoiseConverter(Converter):
    """
    Nudges printable ASCII characters to an adjacent codepoint.

    Each character is shifted one step up or down with probability ``noise_probability``,
    kept inside the printable ASCII range. Non-ASCII characters are left alone. Unlike
    ``NoiseConverter`` this uses no LLM, and each call draws fresh randomness.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, noise_probability: float = 0.05) -> None:
        """
        Args:
            noise_probability (float): Per-character probability in [0.0, 1.0] of
                nudging a character to an adjacent codepoint. Defaults to 0.05.

        Raises:
            ValueError: If ``noise_probability`` is outside [0.0, 1.0].
        """
        if not 0.0 <= noise_probability <= 1.0:
            raise ValueError("noise_probability must be between 0.0 and 1.0")
        self.noise_probability = noise_probability

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(params={"noise_probability": self.noise_probability})

    def _noise(self, text: str) -> str:
        out = []
        for ch in text:
            if not " " <= ch <= "~" or random.random() >= self.noise_probability:
                out.append(ch)
                continue

            offset = 1 if ch == " " else -1 if ch == "~" else random.choice((-1, 1))
            ch = chr(ord(ch) + offset)
            out.append(ch)
        return "".join(out)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Apply one fresh pass of ASCII noise to the prompt.

        Args:
            prompt (str): The text prompt to perturb.
            input_type (PromptDataType): The input data type. Only ``text`` is supported.

        Returns:
            ConverterResult: The perturbed prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        return ConverterResult(output_text=self._noise(prompt), output_type="text")
