# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import pathlib
import random
from typing import Optional

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.models import PromptDataType, SeedPrompt
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class TemplateSegmentConverter(PromptConverter):
    """
    Uses a template to randomly split a prompt into segments defined by the template.

    This converter is a generalized version of this:
    https://adversa.ai/blog/universal-llm-jailbreak-chatgpt-gpt-4-bard-bing-anthropic-and-beyond/
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        prompt_template: Optional[SeedPrompt] = None,
    ):
        """
        Initialize the converter with the specified target and prompt template.

        Args:
            prompt_template (SeedPrompt, Optional): The prompt template for the conversion. Must have two or more
                parameters. If not provided, uses the default ``tom_and_jerry.yaml`` template.

        Raises:
            ValueError: If the template has fewer than two parameters or if any parameter is missing in the template.
        """
        super().__init__()

        self.prompt_template = (
            prompt_template
            if prompt_template
            else SeedPrompt.from_yaml_file(
                pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "template_segment_converter" / "tom_and_jerry.yaml"
            )
        )

        self._number_parameters = len(self.prompt_template.parameters)

        if self._number_parameters < 2:
            raise ValueError(
                f"Template must have at least two parameters, but found {len(self.prompt_template.parameters)}. "
                f"Template parameters: {self.prompt_template.parameters}"
            )

        # Validate all parameters exist in the template value by attempting to render with empty values
        try:
            # Create a dict with empty values for all parameters
            empty_values = {param: "" for param in self.prompt_template.parameters}
            # This will raise ValueError if any parameter is missing
            self.prompt_template.render_template_value(**empty_values)
        except ValueError as e:
            raise ValueError(
                f"Error validating template parameters: {str(e)}. "
                f"Template parameters: {self.prompt_template.parameters}"
            )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by splitting it into random segments and using them to fill the template parameters.
        The prompt is split into N segments (where N is the number of template parameters) at random word boundaries.
        Each segment is then used to fill the corresponding template parameter.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the template filled with prompt segments.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        segments = self._split_prompt_into_segments(prompt)
        filled_template = self.prompt_template.render_template_value(
            **dict(zip(self.prompt_template.parameters, segments))
        )
        return ConverterResult(output_text=filled_template, output_type="text")

    def _split_prompt_into_segments(self, prompt: str) -> list[str]:
        """
        Split a prompt into random segments based on word boundaries.
        If there aren't enough words for all parameters, remaining segments will be empty strings.

        Args:
            prompt (str): The prompt to split into segments.

        Returns:
            list[str]: List of segments, padded with empty strings if needed.
        """
        words = prompt.split()
        num_splits = min(len(words), self._number_parameters - 1)

        # Handle edge case where we can't sample from an empty range
        if num_splits > 0 and len(words) > 1:
            split_points = sorted(random.sample(range(1, len(words)), num_splits))
        else:
            split_points = []

        split_points = [0] + split_points + [len(words)]  # Add start and end points

        # Create segments by joining words between split points
        segments = []
        for i in range(len(split_points) - 1):
            segment = " ".join(words[split_points[i] : split_points[i + 1]])
            segments.append(segment)

        # Pad with empty strings if we don't have enough segments
        segments.extend([""] * (self._number_parameters - len(segments)))
        return segments
