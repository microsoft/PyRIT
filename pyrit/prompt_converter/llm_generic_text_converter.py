# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from typing import Any, Optional

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.models import (
    Message,
    MessagePiece,
    PromptDataType,
    SeedPrompt,
)
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter
from pyrit.prompt_target import PromptChatTarget

logger = logging.getLogger(__name__)


class LLMGenericTextConverter(PromptConverter):
    """
    Represents a generic LLM converter that expects text to be transformed (e.g. no JSON parsing or format).
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptChatTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        system_prompt_template: Optional[SeedPrompt] = None,
        user_prompt_template_with_objective: Optional[SeedPrompt] = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the converter with a target and optional prompt templates.

        Args:
            converter_target (PromptChatTarget): The endpoint that converts the prompt.
                Can be omitted if a default has been configured via PyRIT initialization.
            system_prompt_template (SeedPrompt, Optional): The prompt template to set as the system prompt.
            user_prompt_template_with_objective (SeedPrompt, Optional): The prompt template to set as the user prompt.
                expects
            kwargs: Additional parameters for the prompt template.

        Raises:
            ValueError: If converter_target is not provided and no default has been configured.
        """
        self._converter_target = converter_target
        self._system_prompt_template = system_prompt_template
        self._prompt_kwargs = kwargs

        if user_prompt_template_with_objective and (
            user_prompt_template_with_objective.parameters is None
            or "objective" not in user_prompt_template_with_objective.parameters
        ):
            raise ValueError("user_prompt_template_with_objective must contain the 'objective' parameter")

        self._user_prompt_template_with_objective = user_prompt_template_with_objective

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt using an LLM via the specified converter target.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the converted output and its type.

        Raises:
            ValueError: If the input type is not supported.
        """
        conversation_id = str(uuid.uuid4())

        kwargs = self._prompt_kwargs.copy()

        if self._system_prompt_template:
            system_prompt = self._system_prompt_template.render_template_value(**kwargs)

            self._converter_target.set_system_prompt(
                system_prompt=system_prompt,
                conversation_id=conversation_id,
                attack_identifier=None,
            )

        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        if self._user_prompt_template_with_objective:
            prompt = self._user_prompt_template_with_objective.render_template_value(objective=prompt)

        request = Message(
            [
                MessagePiece(
                    role="user",
                    original_value=prompt,
                    conversation_id=conversation_id,
                    sequence=1,
                    prompt_target_identifier=self._converter_target.get_identifier(),
                    original_value_data_type=input_type,
                    converted_value_data_type=input_type,
                    converter_identifiers=[self.get_identifier()],
                )
            ]
        )

        response = await self._converter_target.send_prompt_async(message=request)
        return ConverterResult(output_text=response[0].get_value(), output_type="text")
