# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import pathlib
import uuid

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


class PersuasionConverter(PromptConverter):
    """
    Rephrases prompts using a variety of persuasion techniques.

    Based on https://arxiv.org/abs/2401.06373 by Zeng et al.

    Supported persuasion techniques:
        - "authority_endorsement":
            Citing authoritative sources in support of a claim.
        - "evidence_based":
            Using empirical data, statistics, and facts to support a claim or decision.
        - "expert_endorsement":
            Citing domain experts in support of a claim.
        - "logical_appeal":
            Using logic or reasoning to support a claim.
        - "misrepresentation":
            Presenting oneself or an issue in a way that's not genuine or true.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptChatTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        persuasion_technique: str,
    ):
        """
        Initialize the converter with the specified target and prompt template.

        Args:
            converter_target (PromptChatTarget): The chat target used to perform rewriting on user prompts.
                Can be omitted if a default has been configured via PyRIT initialization.
            persuasion_technique (str): Persuasion technique to be used by the converter, determines the system prompt
                to be used to generate new prompts. Must be one of "authority_endorsement", "evidence_based",
                "expert_endorsement", "logical_appeal", "misrepresentation".

        Raises:
            ValueError: If converter_target is not provided and no default has been configured.
            ValueError: If the persuasion technique is not supported or does not exist.
        """
        self.converter_target = converter_target

        try:
            prompt_template = SeedPrompt.from_yaml_file(
                pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "persuasion" / f"{persuasion_technique}.yaml"
            )
        except FileNotFoundError:
            raise ValueError(f"Persuasion technique '{persuasion_technique}' does not exist or is not supported.")
        self.system_prompt = str(prompt_template.value)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using the persuasion technique specified during initialization.

        Args:
            prompt (str): The input prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted prompt text.

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

        response = await self.send_persuasion_prompt_async(request)

        return ConverterResult(output_text=response, output_type="text")

    @pyrit_json_retry
    async def send_persuasion_prompt_async(self, request: Message) -> str:
        """
        Send the prompt to the converter target and process the response.

        Args:
            request (Message): The message containing the prompt to be converted.

        Returns:
            str: The converted prompt text extracted from the response.

        Raises:
            InvalidJsonException: If the response is not valid JSON or missing expected keys.
        """
        response = await self.converter_target.send_prompt_async(message=request)

        response_msg = response[0].get_value()
        response_msg = remove_markdown_json(response_msg)

        try:
            parsed_response = json.loads(response_msg)
            if "mutated_text" not in parsed_response:
                raise InvalidJsonException(
                    message=f"Invalid JSON encountered; missing 'mutated_text' key: {response_msg}"
                )
            return str(parsed_response["mutated_text"])

        except json.JSONDecodeError:
            raise InvalidJsonException(message=f"Invalid JSON encountered: {response_msg}")
