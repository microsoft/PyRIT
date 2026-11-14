# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import pathlib

from pyrit.common import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.models import SeedPrompt
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class AdversarialPoetryConverter(LLMGenericTextConverter):
    """
    Rewrites a prompt as a short poem that ends with a plain instruction line, using an LLM.

    Based on "Adversarial Poetry as a Universal Single-Turn Jailbreak Mechanism in Large Language
    Models" (Bisconti et al., arXiv:2511.15304), which found that the poetic form alone raises
    single-turn attack success on many models. The paper does not publish its meta-prompt, so the
    default template follows the constraints the paper describes rather than reproducing it.

    An existing ``PromptTarget`` is used to perform the conversion (like Azure OpenAI).
    """

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        prompt_template: SeedPrompt | None = None,
    ) -> None:
        """
        Initialize the converter with the target that writes the poem and an optional prompt template.

        Args:
            converter_target (PromptTarget): The chat target that rewrites the prompt as a poem.
                Can be omitted if a default has been configured via PyRIT initialization.
            prompt_template (SeedPrompt, Optional): The prompt template for the conversion. Defaults to
                ``adversarial_poetry_converter.yaml``.
        """
        prompt_template = (
            prompt_template
            if prompt_template
            else SeedPrompt.from_yaml_file(
                pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "adversarial_poetry_converter.yaml"
            )
        )

        super().__init__(converter_target=converter_target, system_prompt_template=prompt_template)
