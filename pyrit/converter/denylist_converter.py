# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import json
import logging
import pathlib

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter.converter import ConverterResult
from pyrit.converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.models import ComponentIdentifier, PromptDataType, SeedPrompt
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class DenylistConverter(LLMGenericTextConverter):
    """
    Replaces forbidden words or phrases in a prompt with synonyms using an LLM.

    An existing ``PromptTarget`` is used to perform the conversion (like Azure OpenAI).
    """

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        system_prompt_template: SeedPrompt | None = None,
        denylist: list[str] | None = None,
    ) -> None:
        """
        Initialize the converter with a target, an optional system prompt template, and a denylist.

        Args:
            converter_target (PromptTarget): The target for the prompt conversion.
                Can be omitted if a default has been configured via PyRIT initialization.
            system_prompt_template (SeedPrompt | None): The system prompt template to use for the conversion.
                If not provided, a default template will be used.
            denylist (list[str]): A list of words or phrases that should be replaced in the prompt.
        """
        # set to default strategy if not provided
        if denylist is None:
            denylist = []
        system_prompt_template = (
            system_prompt_template
            if system_prompt_template
            else SeedPrompt.from_yaml_file(pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "denylist_converter.yaml")
        )

        self._denylist = denylist

        super().__init__(
            converter_target=converter_target, system_prompt_template=system_prompt_template, denylist=denylist
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the denylist.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        # The denylist is rendered into the system prompt, so it is part of the converter's identity.
        denylist_hash = None
        if self._denylist:
            denylist_hash = hashlib.sha256(json.dumps(self._denylist).encode("utf-8")).hexdigest()[:16]

        base_identifier = super()._build_identifier()
        return self._create_identifier(
            params={**base_identifier.params, "denylist_hash": denylist_hash},
            converter_target=self._converter_target.get_identifier(),
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by removing any words or phrases that are in the denylist,
        replacing them with synonymous words.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the modified prompt.
        """
        # check if the prompt contains any words from the  denylist and if so,
        # update the prompt replacing the denied words with synonyms
        denylist = self._prompt_kwargs.get("denylist", [])
        if any(word in prompt for word in denylist):
            return await super().convert_async(prompt=prompt, input_type=input_type)
        logger.info(f"Prompt does not contain any words from the denylist. prompt: {prompt}, denylist: {denylist}")
        return ConverterResult(output_text=prompt, output_type=input_type)
