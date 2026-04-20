# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import uuid
from typing import Any

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
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
from pyrit.prompt_converter import ConverterResult, PromptConverter
from pyrit.prompt_target import PromptChatTarget

logger = logging.getLogger(__name__)


class FuzzerConverter(PromptConverter):
    """
    Base class for GPTFUZZER converters.

    Adapted from GPTFUZZER: Red Teaming Large Language Models with Auto-Generated Jailbreak Prompts.
    Paper: https://arxiv.org/pdf/2309.10253 by Jiahao Yu, Xingwei Lin, Zheng Yu, Xinyu Xing.
    GitHub: https://github.com/sherdencooper/GPTFuzz/tree/master
    """

    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("text",)

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptChatTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        prompt_template: SeedPrompt,
    ):
        """
        Initialize the converter with the specified chat target and prompt template.

        Args:
            converter_target (PromptChatTarget): Chat target used to perform fuzzing on user prompt.
                Can be omitted if a default has been configured via PyRIT initialization.
            prompt_template (SeedPrompt): Template to be used instead of the default system prompt with
                instructions for the chat target.

        Raises:
            ValueError: If converter_target is not provided and no default has been configured.
        """
        self.converter_target = converter_target
        self.system_prompt = prompt_template.value
        self.template_label = "TEMPLATE"

    def update(self, **kwargs: Any) -> None:
        """Update the converter with new parameters."""
        pass

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt into the target format supported by the converter.

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

        return ConverterResult(output_text=response, output_type="text")

    @pyrit_json_retry
    async def send_prompt_async(self, request: Message) -> str:
        """
        Send the message to the converter target and process the response.

        Args:
            request: The message request to send.

        Returns:
            str: The output from the parsed JSON response.

        Raises:
            InvalidJsonException: If the response is not valid JSON or missing required keys.
        """
        response = await self.converter_target.send_prompt_async(message=request)

        response_msg = response[0].get_value()
        response_msg = remove_markdown_json(response_msg)

        try:
            parsed_response = json.loads(response_msg)
            if "output" not in parsed_response:
                raise InvalidJsonException(message=f"Invalid JSON encountered; missing 'output' key: {response_msg}")
            return str(parsed_response["output"])

        except json.JSONDecodeError:
            raise InvalidJsonException(message=f"Invalid JSON encountered: {response_msg}")

    def input_supported(self, input_type: PromptDataType) -> bool:
        """
        Check if the input type is supported.

        Returns:
            bool: True if input type is text, False otherwise.
        """
        return input_type == "text"

    def output_supported(self, output_type: PromptDataType) -> bool:
        """
        Check if the output type is supported.

        Returns:
            bool: True if output type is text, False otherwise.
        """
        return output_type == "text"
