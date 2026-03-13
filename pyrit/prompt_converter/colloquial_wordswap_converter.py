# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
import random
import re
import warnings
from typing import Optional

import yaml

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class ColloquialWordswapConverter(PromptConverter):
    """
    Converts text by replacing words with regional colloquial alternatives.

    Supports loading substitutions from YAML files (e.g., Singaporean, Filipino, Indian)
    or accepting a custom substitution dictionary. Defaults to Singaporean substitutions.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *args: bool,
        deterministic: bool = False,
        custom_substitutions: Optional[dict[str, list[str]]] = None,
        wordswap_path: Optional[str] = None,
    ) -> None:
        """
        Initialize the converter with optional deterministic mode and substitutions source.

        Args:
            *args: Deprecated positional argument for deterministic. Use deterministic=... instead.
            deterministic (bool): If True, use the first substitution for each wordswap.
                If False, randomly choose a substitution for each wordswap. Defaults to False.
            custom_substitutions (Optional[dict[str, list[str]]]): A dictionary of custom substitutions
                to override the defaults. Defaults to None.
            wordswap_path (Optional[str]): Path to a YAML file containing word substitutions.
                Can be a filename within the built-in colloquial_wordswaps directory (e.g., "filipino.yaml")
                or an absolute path to a custom YAML file. Defaults to None (uses singaporean.yaml).

        Raises:
            ValueError: If both custom_substitutions and wordswap_path are provided,
                or if the YAML file has an invalid format.
            FileNotFoundError: If the specified wordswap YAML file does not exist.
        """
        if args:
            warnings.warn(
                "Passing 'deterministic' as a positional argument is deprecated. "
                "Use deterministic=... as a keyword argument. "
                "It will be keyword-only starting in version 0.13.0.",
                FutureWarning,
                stacklevel=2,
            )
            deterministic = args[0]
        if custom_substitutions is not None and wordswap_path is not None:
            raise ValueError("Provide either custom_substitutions or wordswap_path, not both.")

        self._wordswap_path = wordswap_path

        if custom_substitutions is not None and len(custom_substitutions) > 0:
            self._colloquial_substitutions = custom_substitutions
        else:
            wordswap_directory = CONVERTER_SEED_PROMPT_PATH / "colloquial_wordswaps"

            if wordswap_path is not None:
                file_path = pathlib.Path(wordswap_path)
                if not file_path.is_absolute():
                    file_path = wordswap_directory / wordswap_path
            else:
                file_path = wordswap_directory / "singaporean.yaml"

            if not file_path.exists():
                raise FileNotFoundError(f"Colloquial wordswap file not found: {file_path}")

            try:
                with file_path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
            except yaml.YAMLError as exc:
                raise ValueError(f"Invalid YAML format in wordswap file: {file_path}") from exc

            if not isinstance(data, dict):
                raise ValueError("Wordswap YAML must be a dict[str, list[str]] mapping words to substitutions.")

            for key, value in data.items():
                if not isinstance(key, str) or not isinstance(value, list) or len(value) == 0:
                    raise ValueError(
                        f"Invalid entry in wordswap YAML: key={key!r}. "
                        "Each key must be a string and each value a non-empty list of strings."
                    )

            self._colloquial_substitutions = data

        self._deterministic = deterministic

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with colloquial wordswap parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "deterministic": self._deterministic,
                "wordswap_path": self._wordswap_path,
                "substitution_keys": sorted(self._colloquial_substitutions.keys()),
            }
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by replacing words with regional colloquial alternatives.

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
