# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import pathlib

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, SeedPrompt
from pyrit.prompt_converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class TranslationConverter(LLMGenericTextConverter):
    """
    Translates prompts into different languages using an LLM.
    """

    RETRY_EXCEPTIONS = (Exception,)

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        language: str,
        prompt_template: SeedPrompt | None = None,
        max_retries: int = 3,
        max_wait_time_in_seconds: int = 60,
    ) -> None:
        """
        Initialize the converter with the target chat support, language, and optional prompt template.

        Args:
            converter_target (PromptTarget): The target chat support for the conversion which will translate.
                Can be omitted if a default has been configured via PyRIT initialization.
            language (str): The language for the conversion. E.g. Spanish, French, leetspeak, etc.
            prompt_template (SeedPrompt | None): The prompt template for the conversion.
            max_retries (int): Maximum number of retry attempts on failure.
            max_wait_time_in_seconds (int): Upper bound for exponential backoff between retries.

        Raises:
            ValueError: If converter_target is not provided and no default has been configured.
            ValueError: If the language is not provided.
        """
        if not language:
            raise ValueError("Language must be provided for translation conversion")

        system_prompt_template = (
            prompt_template
            if prompt_template
            else SeedPrompt.from_yaml_file(pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "translation_converter.yaml")
        )

        user_prompt_template = SeedPrompt.from_yaml_file(
            pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "translation_user_prompt.yaml"
        )

        self.language = language.lower()
        self.system_prompt = system_prompt_template.render_template_value(language=language)

        super().__init__(
            converter_target=converter_target,
            system_prompt_template=system_prompt_template,
            user_prompt_template_with_objective=user_prompt_template,
            max_retry_attempts=max_retries,
            retry_wait_max_seconds=max_wait_time_in_seconds,
            language=self.language,
        )
        self.converter_target = converter_target

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with translation parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "language": self.language,
            },
            converter_target=self.converter_target.get_identifier(),
        )

    def _process_response(self, response_text: str) -> str:
        """
        Strip surrounding whitespace from the LLM response.

        Args:
            response_text (str): The raw text returned by the LLM.

        Returns:
            str: The trimmed response text.
        """
        return response_text.strip()
