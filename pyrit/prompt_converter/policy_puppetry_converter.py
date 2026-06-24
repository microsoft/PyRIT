# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import logging
import pathlib
from typing import Literal

import yaml

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, PromptDataType, SeedPrompt
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class PolicyPuppetryConverter(PromptConverter):
    """
    Wraps a prompt in a fake structured "policy" block to attempt a jailbreak.

    Implements HiddenLayer's "Policy Puppetry Attack" (HiddenLayer, Apr 2025), a universal
    prompt-injection technique that frames the user's request as a configuration "policy"
    (rendered as XML, JSON, or INI) combined with roleplay framing. The structured block
    mimics formats the model may have been trained to treat as authoritative, encouraging it
    to comply with the embedded request.

    To additionally obfuscate keywords (e.g. leetspeak), compose this converter with
    ``LeetspeakConverter`` in a converter chain rather than configuring it here.

    This is a pure-template converter and requires no LLM or network access.

    See: https://hiddenlayer.com/innovation-hub/novel-universal-bypass-for-all-major-llms/
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        policy_format: Literal["xml", "json", "ini"] = "xml",
    ) -> None:
        """
        Initialize the converter with the desired policy format.

        Args:
            policy_format (Literal["xml", "json", "ini"]): The structured format used to render the
                fake policy block wrapping the prompt. Defaults to ``"xml"``.
        """
        super().__init__()

        self._policy_format = policy_format

        # Load the raw YAML so the template's Jinja control structures (the per-format
        # ``{% if policy_format ... %}`` branches) are preserved. ``SeedPrompt.from_yaml_file``
        # marks the seed as a trusted template and eagerly pre-renders it at construction time,
        # which would collapse the conditional to a single branch before ``policy_format`` is known.
        template_data = yaml.safe_load(
            (pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "policy_puppetry_converter.yaml").read_text(encoding="utf-8")
        )
        self._prompt_template = SeedPrompt(**template_data)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with policy format and obfuscation parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        template_hash = hashlib.sha256(str(self._prompt_template.value).encode("utf-8")).hexdigest()[:16]
        return self._create_identifier(
            params={
                "policy_format": self._policy_format,
                "template_hash": template_hash,
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Wrap the given prompt in a fake policy block.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the prompt wrapped in the policy block.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        wrapped = self._prompt_template.render_template_value(
            prompt=prompt,
            policy_format=self._policy_format,
        )

        return ConverterResult(output_text=wrapped, output_type="text")
