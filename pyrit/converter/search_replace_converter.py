# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType


class SearchReplaceConverter(Converter):
    """
    Converts a string by replacing chosen phrase with a new phrase of choice.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, pattern: str, replace: str | list[str], regex_flags: int = 0) -> None:
        """
        Initialize the converter with the specified regex pattern and replacement phrase(s).

        Args:
            pattern (str): The regex pattern to replace. Empty patterns match at every character boundary.
            replace (str | list[str]): The new phrase to replace with. Can be a single string or a list of strings.
                If a list is provided, a random element will be chosen for replacement.
            regex_flags (int): Regex flags to use for the replacement. Defaults to 0 (no flags).

        Raises:
            ValueError: If ``pattern`` is not a string or contains invalid regex syntax,
                ``replace`` is not a string or non-empty list of strings, or ``regex_flags`` is not an integer.
        """
        if not isinstance(pattern, str):
            raise ValueError("pattern must be a string.")

        if not isinstance(regex_flags, int) or isinstance(regex_flags, bool):
            raise ValueError("regex_flags must be an integer.")

        try:
            self._compiled_regex = re.compile(pattern, flags=regex_flags)
        except (re.error, OverflowError) as e:
            raise ValueError(f"Invalid regex pattern or flags: {e}") from e

        if isinstance(replace, str):
            self._replace_list = [replace]
        elif isinstance(replace, list):
            if not replace:
                raise ValueError("replace cannot be an empty list.")
            if not all(isinstance(item, str) for item in replace):
                raise ValueError("All items in replace list must be strings.")
            self._replace_list = list(replace)
        else:
            raise ValueError("replace must be a string or a non-empty list of strings.")

        self._pattern = pattern
        self._regex_flags = regex_flags

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with search/replace parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "pattern": self._pattern,
                "replace_list": self._replace_list,
                "regex_flags": self._regex_flags,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by replacing the specified pattern with a random choice from the replacement list.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted text.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        replace = self._get_random_generator(stream="replacement").choice(self._replace_list)

        return ConverterResult(
            output_text=self._compiled_regex.sub(replace, prompt),
            output_type="text",
        )
