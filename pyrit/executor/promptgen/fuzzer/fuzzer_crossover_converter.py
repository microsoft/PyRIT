# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
import random
import uuid
from typing import Any, List, Optional

from pyrit.common.apply_defaults import apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.executor.promptgen.fuzzer.fuzzer_converter_base import (
    FuzzerConverter,
)
from pyrit.models import Message, MessagePiece, PromptDataType, SeedPrompt
from pyrit.prompt_converter.prompt_converter import ConverterResult
from pyrit.prompt_target import PromptChatTarget


class FuzzerCrossOverConverter(FuzzerConverter):
    """
    Uses multiple prompt templates to generate new prompts.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: Optional[PromptChatTarget] = None,
        prompt_template: Optional[SeedPrompt] = None,
        prompt_templates: Optional[List[str]] = None,
    ):
        """
        Initialize the converter with the specified chat target and prompt templates.

        Args:
            converter_target (PromptChatTarget): Chat target used to perform fuzzing on user prompt.
                Can be omitted if a default has been configured via PyRIT initialization.
            prompt_template (SeedPrompt, Optional): Template to be used instead of the default system prompt with
                instructions for the chat target.
            prompt_templates (List[str], Optional): List of prompt templates to use in addition to the default one.
        """
        prompt_template = (
            prompt_template
            if prompt_template
            else SeedPrompt.from_yaml_file(
                pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "fuzzer_converters" / "crossover_converter.yaml"
            )
        )
        super().__init__(converter_target=converter_target, prompt_template=prompt_template)
        self.prompt_templates = prompt_templates or []
        self.template_label = "TEMPLATE 1"

    def update(self, **kwargs: Any) -> None:
        """Update the converter with new prompt templates."""
        if "prompt_templates" in kwargs:
            self.prompt_templates = kwargs["prompt_templates"]

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by combining it with a random prompt template from the list of available templates.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the modified prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        if len(self.prompt_templates) == 0:
            raise ValueError(
                "No prompt templates available for crossover. Please provide prompt templates via the update method."
            )

        conversation_id = str(uuid.uuid4())

        self.converter_target.set_system_prompt(
            system_prompt=self.system_prompt,
            conversation_id=conversation_id,
            attack_identifier=None,
        )

        formatted_prompt = f"===={self.template_label} BEGINS====\n{prompt}\n===={self.template_label} ENDS===="
        formatted_prompt += (
            f"\n====TEMPLATE 2 BEGINS====\n{random.choice(self.prompt_templates)}\n====TEMPLATE 2 ENDS====\n"
        )

        prompt_metadata: dict[str, str | int] = {"response_format": "json"}
        request = Message(
            [
                MessagePiece(
                    role="user",
                    original_value=formatted_prompt,
                    converted_value=formatted_prompt,
                    conversation_id=conversation_id,
                    sequence=1,
                    prompt_target_identifier=self.converter_target.get_identifier(),
                    original_value_data_type=input_type,
                    converted_value_data_type=input_type,
                    converter_identifiers=[self.get_identifier()],
                    prompt_metadata=prompt_metadata,
                )
            ]
        )

        response = await self.send_prompt_async(request)

        return ConverterResult(output_text=response, output_type="text")
