# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

from pyrit.common.text_helper import graphemes
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import PromptDataType

_PUNCTUATION_PATTERN = re.compile("[!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~]")


class CharacterSpaceConverter(Converter):
    """
    Spaces out the input prompt and removes ASCII punctuation except backslashes.

    Characters are grapheme clusters, so combining marks, Indic vowel signs and emoji sequences stay whole.

    For more information on the bypass strategy, refer to [@robustintelligence2024bypass].
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by removing punctuation and spacing out characters.

        Args:
            prompt (str): The input text prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted text.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")
        # Split before removing punctuation, so a keycap emoji such as #\ufe0f\u20e3 stays whole
        clusters = [cluster for cluster in graphemes(prompt) if not _PUNCTUATION_PATTERN.fullmatch(cluster)]
        converted_text = " ".join(clusters)
        return ConverterResult(output_text=converted_text, output_type="text")
