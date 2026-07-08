# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import pathlib

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.exceptions import (
    InvalidJsonException,
    remove_markdown_json,
)
from pyrit.models import (
    ComponentIdentifier,
    SeedPrompt,
)
from pyrit.prompt_converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class VariationConverter(LLMGenericTextConverter):
    """
    Generates variations of the input prompts using the converter target.
    """

    RETRY_EXCEPTIONS = (InvalidJsonException,)

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        prompt_template: SeedPrompt | None = None,
    ) -> None:
        """
        Initialize the converter with the specified target and prompt template.

        Args:
            converter_target (PromptTarget): The target to which the prompt will be sent for conversion.
                Can be omitted if a default has been configured via PyRIT initialization.
            prompt_template (SeedPrompt | None): The template used for generating the system prompt.
                If not provided, a default template will be used.

        Raises:
            ValueError: If converter_target is not provided and no default has been configured.
        """
        system_prompt_template = (
            prompt_template
            if prompt_template
            else SeedPrompt.from_yaml_file(pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "variation_converter.yaml")
        )

        user_prompt_template = SeedPrompt.from_yaml_file(
            pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "variation_user_prompt.yaml"
        )

        self.number_variations = 1
        self.system_prompt = str(
            system_prompt_template.render_template_value(number_iterations=str(self.number_variations))
        )

        super().__init__(
            converter_target=converter_target,
            system_prompt_template=system_prompt_template,
            user_prompt_template_with_objective=user_prompt_template,
            number_iterations=str(self.number_variations),
        )
        self.converter_target = converter_target

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with variation parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            converter_target=self.converter_target.get_identifier(),
        )

    def _process_response(self, response_text: str) -> str:
        """
        Parse the JSON list response and return the first variation.

        Args:
            response_text (str): The raw text returned by the LLM.

        Returns:
            str: The first variation extracted from the JSON list.

        Raises:
            InvalidJsonException: If the response is not valid JSON or does not contain the expected list shape.
        """
        cleaned = remove_markdown_json(response_text)
        try:
            parsed = json.loads(cleaned)
            return str(parsed[0])
        except (json.JSONDecodeError, IndexError, KeyError, TypeError):
            raise InvalidJsonException(message=f"Invalid JSON response: {cleaned}") from None
