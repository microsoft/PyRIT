# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

from pyrit.common import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import DATASETS_PATH
from pyrit.converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.models import ComponentIdentifier, SeedPrompt
from pyrit.prompt_target import PromptTarget


class IPAConverter(LLMGenericTextConverter):
    """
    Converts text to broad International Phonetic Alphabet (IPA) transcription using an LLM.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        dialect: str | None = None,
    ) -> None:
        """
        Initialize the converter with the target and optional pronunciation dialect.

        Args:
            converter_target (PromptTarget): The target that performs the transcription.
            dialect (str | None): The language variety or dialect to use for pronunciation.
                When omitted, the converter infers the language and dialect from the input.

        Raises:
            ValueError: If dialect is provided but empty.
        """
        normalized_dialect = dialect.strip() if dialect is not None else None
        if dialect is not None and not normalized_dialect:
            raise ValueError("dialect must be a non-empty string")

        self._dialect = normalized_dialect
        pronunciation_guidance = (
            f"Use {self._dialect} pronunciation wherever it applies. "
            "Infer the language and pronunciation variety of any other language spans from context."
            if self._dialect
            else "Detect the source language or languages from context and infer the most likely "
            "pronunciation variety for each."
        )
        system_prompt_template = SeedPrompt.from_yaml_file(Path(DATASETS_PATH) / "converters" / "ipa_converter.yaml")
        user_prompt_template = SeedPrompt.from_yaml_file(Path(DATASETS_PATH) / "converters" / "ipa_user_prompt.yaml")

        super().__init__(
            converter_target=converter_target,
            system_prompt_template=system_prompt_template,
            user_prompt_template_with_objective=user_prompt_template,
            pronunciation_guidance=pronunciation_guidance,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier with optional dialect guidance and prompt templates.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        base_identifier = super()._build_identifier()
        return self._create_identifier(
            params={**base_identifier.params, "dialect": self._dialect},
            converter_target=self._converter_target.get_identifier(),
        )

    def _process_response(self, response_text: str) -> str:
        """
        Remove surrounding whitespace from the transcription.

        Args:
            response_text (str): The raw target response.

        Returns:
            str: The stripped transcription.
        """
        return response_text.strip()
