# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
import uuid
from typing import Optional

from pyrit.common.apply_defaults import apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.executor.promptgen.fuzzer.fuzzer_converter_base import (
    FuzzerConverter,
)
from pyrit.models import Message, MessagePiece, PromptDataType, SeedPrompt
from pyrit.prompt_converter.prompt_converter import ConverterResult
from pyrit.prompt_target import PromptChatTarget


class FuzzerExpandConverter(FuzzerConverter):
    """
    Generates versions of a prompt with new, prepended sentences.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: Optional[PromptChatTarget] = None,
        prompt_template: Optional[SeedPrompt] = None,
    ):
        """Initialize the expand converter with optional chat target and prompt template."""
        prompt_template = (
            prompt_template
            if prompt_template
            else SeedPrompt.from_yaml_file(
                pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "fuzzer_converters" / "expand_converter.yaml"
            )
        )
        super().__init__(converter_target=converter_target, prompt_template=prompt_template)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by generating versions of it with new, prepended sentences.

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

        conversation_id = str(uuid.uuid4())

        self.converter_target.set_system_prompt(
            system_prompt=self.system_prompt,
            conversation_id=conversation_id,
            attack_identifier=None,
        )

        formatted_prompt = f"===={self.template_label} BEGINS====\n{prompt}\n===={self.template_label} ENDS===="

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

        return ConverterResult(output_text=response + " " + prompt, output_type="text")
