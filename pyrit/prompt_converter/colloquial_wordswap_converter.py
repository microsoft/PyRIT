# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import random
import re
from typing import Dict, List, Optional

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class ColloquialWordswapConverter(PromptConverter):
    """
    Converts text into colloquial Singaporean context.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self, deterministic: bool = False, custom_substitutions: Optional[Dict[str, List[str]]] = None
    ) -> None:
        """
        Initialize the converter with optional deterministic mode and custom substitutions.

        Args:
            deterministic (bool): If True, use the first substitution for each wordswap.
                If False, randomly choose a substitution for each wordswap. Defaults to False.
            custom_substitutions (Optional[Dict[str, List[str]]], Optional): A dictionary of custom substitutions to
                override the defaults. Defaults to None.
        """
        default_substitutions = {
            "father": ["papa", "lao bei", "lim pei", "bapa", "appa"],
            "mother": ["mama", "amma", "ibu"],
            "grandfather": ["ah gong", "thatha", "dato"],
            "grandmother": ["ah ma", "patti", "nenek"],
            "girl": ["ah ger", "ponnu"],
            "boy": ["ah boy", "boi", "payyan"],
            "son": ["ah boy", "boi", "payyan"],
            "daughter": ["ah ger", "ponnu"],
            "aunt": ["makcik", "maami"],
            "aunty": ["makcik", "maami"],
            "man": ["ah beng", "shuai ge"],
            "woman": ["ah lian", "xiao mei"],
            "uncle": ["encik", "unker"],
            "sister": ["xjj", "jie jie", "zhezhe", "kaka", "akka", "thangatchi"],
            "brother": ["bro", "boiboi", "di di", "xdd", "anneh", "thambi"],
        }

        # Use custom substitutions if provided, otherwise default to the standard ones
        self._colloquial_substitutions = custom_substitutions if custom_substitutions else default_substitutions
        self._deterministic = deterministic

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by replacing words with colloquial Singaporean terms.

        Args:
            prompt (str): The input text prompt to be converted.
            input_type (PromptDataType): The type of the input prompt. Defaults to "text".

        Returns:
            ConverterResult: The result containing the converted prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        # Tokenize the prompt into words and non-words
        words = re.findall(r"\w+|\S+", prompt)
        converted_prompt = []

        for word in words:
            lower_word = word.lower()
            if lower_word in self._colloquial_substitutions:
                if self._deterministic:
                    # Use the first substitution for deterministic mode
                    converted_prompt.append(self._colloquial_substitutions[lower_word][0])
                else:
                    # Randomly select a substitution for each wordswap
                    converted_prompt.append(random.choice(self._colloquial_substitutions[lower_word]))
            else:
                # If word not in substitutions, keep it as is
                converted_prompt.append(word)

        # Join all words and punctuation with spaces
        final_prompt = " ".join(converted_prompt)

        # Clean up spaces for final prompt
        final_prompt = re.sub(r'\s([?.!,\'"])', r"\1", final_prompt)
        final_prompt = final_prompt.strip()

        return ConverterResult(output_text=final_prompt, output_type="text")
