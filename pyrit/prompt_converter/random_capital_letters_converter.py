# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import random

from pyrit.models import PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class RandomCapitalLettersConverter(PromptConverter):
    """Takes a prompt and randomly capitalizes it by a percentage of the total characters."""

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, percentage: float = 100.0) -> None:
        """
        Initialize the converter with the specified percentage of randomization.

        Args:
            percentage (float): The percentage of characters to capitalize in the prompt. Must be between 1 and 100.
                Defaults to 100.0. This includes decimal points in that range.
        """
        self.percentage = percentage

    def is_percentage(self, input_string: float) -> bool:
        """
        Check if the input string is a valid percentage between 1 and 100.

        Args:
            input_string (str): The input string to check.

        Returns:
            bool: True if the input string is a valid percentage, False otherwise.
        """
        try:
            number = float(input_string)
            return 1 <= number <= 100
        except ValueError:
            return False

    def generate_random_positions(self, total_length: int, set_number: int) -> list[int]:
        """
        Generate a list of unique random positions within the range of `total_length`.

        Args:
            total_length (int): The total length of the string.
            set_number (int): The number of unique random positions to generate.

        Returns:
            list: A list of unique random positions.

        Raises:
            ValueError: If `set_number` is greater than `total_length`.
        """
        # Ensure the set number is not greater than the total length
        if set_number > total_length:
            logger.error(f"Set number {set_number} cannot be greater than the total length which is {total_length}.")
            raise ValueError(
                f"Set number {set_number} cannot be greater than the total length which is {total_length}."
            )

        # Generate a list of unique random positions
        random_positions = random.sample(range(total_length), set_number)

        return random_positions

    def string_to_upper_case_by_percentage(self, percentage: float, prompt: str) -> str:
        """
        Convert a string by randomly capitalizing a percentage of its characters.

        Args:
            percentage (float): The percentage of characters to capitalize.
            prompt (str): The input string to be converted.

        Returns:
            str: The converted string with randomly capitalized characters.

        Raises:
            ValueError: If the percentage is not between 1 and 100.
        """
        if not self.is_percentage(percentage):
            logger.error(f"Percentage number {percentage} cannot be higher than 100 and lower than 1.")
            raise ValueError(f"Percentage number {percentage} cannot be higher than 100 and lower than 1.")
        target_count = int(len(prompt) * (percentage / 100))
        random_positions = self.generate_random_positions(len(prompt), target_count)
        output = list(prompt)
        for pos in random_positions:
            if prompt[pos].islower():
                output[pos] = prompt[pos].upper()
        return "".join(output)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by randomly capitalizing a percentage of its characters.

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

        output = self.string_to_upper_case_by_percentage(self.percentage, prompt)
        return ConverterResult(output_text=output, output_type="text")
