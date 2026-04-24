# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
from typing import Optional

from pyrit.common.apply_defaults import apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.executor.promptgen.fuzzer.fuzzer_converter_base import (
    FuzzerConverter,
)
from pyrit.models import SeedPrompt
from pyrit.prompt_target import PromptChatTarget


class FuzzerSimilarConverter(FuzzerConverter):
    """
    Generates versions of a prompt with similar sentences.
    """

    @apply_defaults
    def __init__(
        self, *, converter_target: Optional[PromptChatTarget] = None, prompt_template: Optional[SeedPrompt] = None
    ):
        """Initialize the similar converter with optional chat target and prompt template."""
        prompt_template = (
            prompt_template
            if prompt_template
            else SeedPrompt.from_yaml_file(
                pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "fuzzer_converters" / "similar_converter.yaml"
            )
        )
        super().__init__(converter_target=converter_target, prompt_template=prompt_template)
