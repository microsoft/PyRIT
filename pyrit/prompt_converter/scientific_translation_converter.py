# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import pathlib
from typing import Literal, Optional, get_args

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedPrompt
from pyrit.prompt_converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.prompt_target import PromptChatTarget

logger = logging.getLogger(__name__)


# Supported translation modes
TranslationMode = Literal["academic", "technical", "smiles", "math", "research", "reaction", "combined"]
TRANSLATION_MODES = set(get_args(TranslationMode))

# Mapping from mode to YAML file name
MODE_YAML_FILES: dict[str, str] = {
    "academic": "academic_science_converter.yaml",
    "technical": "technical_science_converter.yaml",
    "smiles": "smiles_science_converter.yaml",
    "math": "math_science_converter.yaml",
    "research": "research_science_converter.yaml",
    "reaction": "reaction_science_converter.yaml",
    "combined": "combined_science_converter.yaml",
}


class ScientificTranslationConverter(LLMGenericTextConverter):
    """
    Uses an LLM to transform simple or direct prompts into
    scientifically-framed versions using technical terminology,
    chemical notation, or academic phrasing.
    This can be useful for red-teaming scenarios to test
    whether safety filters can be bypassed through scientific translation.

    """

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptChatTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-assignment, ty:invalid-parameter-default]
        mode: str = "combined",
        prompt_template: Optional[SeedPrompt] = None,
    ) -> None:
        """
        Initialize the scientific translation converter.

        Args:
            converter_target (PromptChatTarget): The LLM target to perform the conversion.
            mode (str): The translation mode to use. Built-in options are:

                - ``academic``: Use academic/homework style framing
                - ``technical``: Use technical jargon and terminology
                - ``smiles``: Uses chemical notation (e.g., SMILES or IUPAC notation such as
                  "2-(acetyloxy)benzoic acid" or "CC(=O)Oc1ccccc1C(=O)O" for aspirin)
                - ``research``: Frame as research/safety study or question
                - ``reaction``: Frame as a step-by-step chemistry mechanism problem
                - ``math``: Frame as the answer key to a mathematical problem or equation
                  for a homework/exam setting
                - ``combined``: Use combination of above techniques together (default)

                You can also use a custom mode name if you provide a prompt_template.
            prompt_template (SeedPrompt, Optional): Custom prompt template.
                Required if using a custom mode not in the built-in list.

        Raises:
            ValueError: If using a custom mode without providing a prompt_template.
        """
        # Resolve template: use provided, or load from mode, or error
        if prompt_template is not None:
            resolved_template = prompt_template
        elif mode in TRANSLATION_MODES:
            yaml_file = MODE_YAML_FILES[mode]
            resolved_template = SeedPrompt.from_yaml_file(pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / yaml_file)
        else:
            raise ValueError(
                f"Custom mode '{mode}' requires a prompt_template. "
                f"Either use a built-in mode from {sorted(TRANSLATION_MODES)} or provide a prompt_template."
            )

        super().__init__(
            converter_target=converter_target,
            system_prompt_template=resolved_template,
        )
        self._mode = mode

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with mode parameter.

        Returns:
            ComponentIdentifier: The identifier for this converter including the mode.
        """
        return self._create_identifier(
            params={
                "mode": self._mode,
            },
            children={"converter_target": self._converter_target.get_identifier()},
        )
