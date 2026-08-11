# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
import functools
import pathlib
import random

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType, SeedDataset, SeedPrompt

# Parameter name the templates expose for the request to smuggle in.
_PROMPT_PARAMETER = "prompt"

_TEMPLATE_PATH = pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "policy_puppetry_converter.yaml"


class PolicyPuppetryTemplate(enum.Enum):
    """
    Selectable Policy Puppetry templates.

    Each member maps to a named template in ``policy_puppetry_converter.yaml``.
    Callers can reference a member and resolve it to its ``SeedPrompt`` via
    ``to_seed_prompt``.
    """

    DR_HOUSE = "dr_house"
    MEDICAL_ADVISOR = "medical_advisor"

    def to_seed_prompt(self) -> SeedPrompt:
        """
        Load the ``SeedPrompt`` template backing this enum member.

        Returns:
            SeedPrompt: The template prompt for this member.
        """
        return _load_templates()[self.value]

    @classmethod
    def random(cls) -> "PolicyPuppetryTemplate":
        """
        Return a randomly selected template member.

        Returns:
            PolicyPuppetryTemplate: A random member of the enum.
        """
        return random.choice(list(cls))


@functools.lru_cache(maxsize=1)
def _load_templates() -> dict[str, SeedPrompt]:
    """
    Load and cache the Policy Puppetry templates keyed by name.

    Returns:
        dict[str, SeedPrompt]: Mapping of template name to its SeedPrompt.
    """
    dataset = SeedDataset.from_yaml_file(_TEMPLATE_PATH)
    return {str(prompt.name): prompt for prompt in dataset.prompts}


class PolicyPuppetryConverter(Converter):
    """
    Wraps a prompt in a Policy Puppetry prompt-injection template.

    Policy Puppetry is a post-instruction-hierarchy, universal, and transferable
    prompt-injection technique that frames a request as policy/configuration the
    model should follow, bypassing instruction hierarchy and safety guardrails.

    The templates live in ``pyrit/datasets/converters/policy_puppetry_converter.yaml``
    and are referenced via ``PolicyPuppetryTemplate``.

    Reference: [@hiddenlayer2025policypuppetry]
    (https://hiddenlayer.com/innovation-hub/novel-universal-bypass-for-all-major-llms/)
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, prompt_template: SeedPrompt | None = None) -> None:
        """
        Initialize the converter with a Policy Puppetry template.

        Args:
            prompt_template (SeedPrompt | None): The template the prompt is wrapped in. The template
                must expose a single ``{{ prompt }}`` parameter. If not provided, a random template
                from ``PolicyPuppetryTemplate`` is used.
        """
        super().__init__()
        self._prompt_template = prompt_template or PolicyPuppetryTemplate.random().to_seed_prompt()

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier including the selected template.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(params={"template": self._prompt_template.name})

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Wrap the prompt in the configured Policy Puppetry template.

        Args:
            prompt (str): The prompt to wrap.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the templated prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"Input type {input_type} not supported")

        wrapped = self._prompt_template.render_template_value(**{_PROMPT_PARAMETER: prompt})
        return ConverterResult(output_text=wrapped, output_type="text")
