# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import pathlib

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.exceptions import (
    InvalidJsonException,
    remove_markdown_json,
)
from pyrit.models import (
    ComponentIdentifier,
    Message,
    SeedPrompt,
)
from pyrit.prompt_converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class PersuasionConverter(LLMGenericTextConverter):
    """
    Rephrases prompts using a variety of persuasion techniques.

    Based on [@zeng2024persuasion].

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

    RETRY_EXCEPTIONS = (InvalidJsonException,)

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        persuasion_technique: str,
    ) -> None:
        """
        Initialize the converter with the specified target and prompt template.

        Args:
            converter_target (PromptTarget): The chat target used to perform rewriting on user prompts.
                Can be omitted if a default has been configured via PyRIT initialization.
            persuasion_technique (str): Persuasion technique to be used by the converter, determines the system prompt
                to be used to generate new prompts. Must be one of "authority_endorsement", "evidence_based",
                "expert_endorsement", "logical_appeal", "misrepresentation".

        Raises:
            ValueError: If converter_target is not provided and no default has been configured.
            ValueError: If the persuasion technique is not supported or does not exist.
        """
        try:
            system_prompt_template = SeedPrompt.from_yaml_file(
                pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "persuasion" / f"{persuasion_technique}.yaml"
            )
        except FileNotFoundError:
            raise ValueError(
                f"Persuasion technique '{persuasion_technique}' does not exist or is not supported."
            ) from None

        self.system_prompt = str(system_prompt_template.value)
        self._persuasion_technique = persuasion_technique

        super().__init__(
            converter_target=converter_target,
            system_prompt_template=system_prompt_template,
        )
        self.converter_target = converter_target

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with persuasion parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "persuasion_technique": self._persuasion_technique,
            },
            converter_target=self.converter_target.get_identifier(),
        )

    def _process_response(self, response_text: str) -> str:
        """
        Parse the JSON response and extract the ``mutated_text`` field.

        Args:
            response_text (str): The raw text returned by the LLM.

        Returns:
            str: The value of the ``mutated_text`` key.

        Raises:
            InvalidJsonException: If the response is not valid JSON or the ``mutated_text`` key is missing.
        """
        cleaned = remove_markdown_json(response_text)
        try:
            parsed = json.loads(cleaned)
            if "mutated_text" not in parsed:
                raise InvalidJsonException(message=f"Invalid JSON encountered; missing 'mutated_text' key: {cleaned}")
            return str(parsed["mutated_text"])
        except (json.JSONDecodeError, TypeError):
            raise InvalidJsonException(message=f"Invalid JSON encountered: {cleaned}") from None

    async def send_persuasion_prompt_async(self, request: Message) -> str:
        """
        Delegate to the unified retry helper. Deprecated shim retained for backward compatibility.

        Args:
            request (Message): The message to send to the converter target.

        Returns:
            str: The post-processed response text.
        """
        print_deprecation_message(
            old_item="PersuasionConverter.send_persuasion_prompt_async",
            new_item="PersuasionConverter._send_with_retries_async (inherited from LLMGenericTextConverter)",
            removed_in="0.16.0",
        )
        return await self._send_with_retries_async(request)
