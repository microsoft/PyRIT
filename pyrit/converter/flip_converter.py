# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.common.text_helper import graphemes
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType


class FlipConverter(Converter):
    """
    Flips the input text prompt by reversing its extended grapheme clusters.

    For example, "hello me" would be converted to "em olleh". Multi-code-point
    sequences like emoji ZWJ sequences, skin-tone modifiers, combining marks,
    and Indic conjuncts stay together in their perceived clusters.

    Note:
        Reversal is generally self-inverse (i.e. flipping twice returns the original
        string), except for inputs where reversing clusters causes them to re-segment
        upon concatenation (such as unpaired regional indicator symbols or leading
        combining marks).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by reversing the text.

        Args:
            prompt: The prompt to be converted.
            input_type: Type of data.

        Returns:
            The converted text representation of the original prompt with characters reversed.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        rev_prompt = "".join(reversed(graphemes(prompt)))

        return ConverterResult(output_text=rev_prompt, output_type="text")
