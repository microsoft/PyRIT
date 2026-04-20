# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import pathlib
import uuid
from textwrap import dedent
from typing import Optional

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.exceptions import (
    InvalidJsonException,
    pyrit_json_retry,
    remove_markdown_json,
)
from pyrit.models import (
    Message,
    MessagePiece,
    PromptDataType,
    SeedPrompt,
)
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter
from pyrit.prompt_target import PromptChatTarget

logger = logging.getLogger(__name__)


class VariationConverter(PromptConverter):
    """
    Generates variations of the input prompts using the converter target.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptChatTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        prompt_template: Optional[SeedPrompt] = None,
    ):
        """
        Initialize the converter with the specified target and prompt template.

        Args:
            converter_target (PromptChatTarget): The target to which the prompt will be sent for conversion.
                Can be omitted if a default has been configured via PyRIT initialization.
            prompt_template (SeedPrompt, optional): The template used for generating the system prompt.
                If not provided, a default template will be used.

        Raises:
            ValueError: If converter_target is not provided and no default has been configured.
        """
        self.converter_target = converter_target

        # set to default strategy if not provided
        prompt_template = (
            prompt_template
            if prompt_template
            else SeedPrompt.from_yaml_file(pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "variation_converter.yaml")
        )

        self.number_variations = 1

        self.system_prompt = str(prompt_template.render_template_value(number_iterations=str(self.number_variations)))

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by generating variations of it using the converter target.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the generated variations.

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

        prompt = dedent(
            f"Create {self.number_variations} variation of the seed prompt given by the user between the "
            "begin and end tags"
            "=== begin ==="
            f"{prompt}"
            "=== end ==="
        )

        request = Message(
            [
                MessagePiece(
                    role="user",
                    original_value=prompt,
                    converted_value=prompt,
                    conversation_id=conversation_id,
                    sequence=1,
                    prompt_target_identifier=self.converter_target.get_identifier(),
                    original_value_data_type=input_type,
                    converted_value_data_type=input_type,
                    converter_identifiers=[self.get_identifier()],
                )
            ]
        )
        response_msg = await self.send_variation_prompt_async(request)

        return ConverterResult(output_text=response_msg, output_type="text")

    @pyrit_json_retry
    async def send_variation_prompt_async(self, request: Message) -> str:
        """
        Send the message to the converter target and retrieve the response.

        Args:
            request (Message): The message to be sent to the converter target.

        Returns:
            str: The response message from the converter target.

        Raises:
            InvalidJsonException: If the response is not valid JSON or does not contain the expected keys.
        """
        response = await self.converter_target.send_prompt_async(message=request)

        response_msg = response[0].get_value()
        response_msg = remove_markdown_json(response_msg)
        try:
            response = json.loads(response_msg)

        except json.JSONDecodeError:
            raise InvalidJsonException(message=f"Invalid JSON response: {response_msg}")

        try:
            return str(response[0])
        except KeyError:
            raise InvalidJsonException(message=f"Invalid JSON response: {response_msg}")
