# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import pathlib
import random
import uuid
from typing import Optional

import yaml

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    Message,
    MessagePiece,
    PromptDataType,
    SeedPrompt,
)
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter
from pyrit.prompt_target import PromptChatTarget

logger = logging.getLogger(__name__)

IMAGE_FILTER_DIR = pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "image_filter"
_SYSTEM_PROMPT_FILENAME = "image_filter_system_prompt.yaml"


class ImageFilterConverter(PromptConverter):
    """
    LLM-based converter that expands a short objective into a detailed image generation prompt
    using a photographic style filter and scene variation.

    The converter loads a filter YAML file containing style_instructions and a list of variations,
    then uses an LLM to expand the user's objective into a fully styled image generation prompt.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptChatTarget = REQUIRED_VALUE,  # type: ignore[assignment]
        filter_name: str,
        variation: Optional[str] = None,
    ) -> None:
        """
        Initialize the converter with a target LLM, filter name, and optional variation.

        Args:
            converter_target: The LLM endpoint that generates the expanded prompt.
                Can be omitted if a default has been configured via PyRIT initialization.
            filter_name: Name of the filter YAML file (without extension) in the image_filter directory.
            variation: Name of the variation to use (matched by prefix before the colon in the YAML,
                e.g. "Bodycam Footage"). Case-insensitive. If None, a random variation is selected
                on each call to convert_async.

        Raises:
            ValueError: If filter_name does not correspond to an existing YAML file.
            ValueError: If variation does not match any entry in the filter.
        """
        self._converter_target = converter_target
        self._filter_name = filter_name
        self._variation = variation

        # Load the shared system prompt template
        system_prompt_path = IMAGE_FILTER_DIR / _SYSTEM_PROMPT_FILENAME
        self._system_prompt_template = SeedPrompt.from_yaml_file(system_prompt_path)

        # Load the filter-specific YAML
        filter_path = IMAGE_FILTER_DIR / f"{filter_name}.yaml"
        if not filter_path.exists():
            available = self.list_available_filters()
            raise ValueError(f"Filter '{filter_name}' not found. Available filters: {available}")

        with open(filter_path, encoding="utf-8") as f:
            filter_data = yaml.safe_load(f)

        self._style_instructions: str = filter_data["style_instructions"]
        self._variations: list[str] = filter_data["variations"]

        # Build a lookup map from variation name prefix (before ":") to full variation string
        self._variation_map: dict[str, str] = {}
        for v in self._variations:
            name = v.split(":", 1)[0].strip().lower()
            self._variation_map[name] = v

        if variation is not None:
            key = variation.strip().lower()
            if key not in self._variation_map:
                available_names = [v.split(":", 1)[0].strip() for v in self._variations]
                raise ValueError(
                    f"Variation '{variation}' not found in filter '{filter_name}'. "
                    f"Available variations: {available_names}"
                )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with filter and variation parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter instance.
        """
        return self._create_identifier(
            params={
                "filter_name": self._filter_name,
                "variation": self._variation,
            },
            children={"converter_target": self._converter_target.get_identifier()},
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert a short objective into a detailed, styled image generation prompt.

        Args:
            prompt (str): The user's short objective (e.g., "two people on a beach applying sunscreen").
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult containing the expanded image generation prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        # Select variation
        if self._variation is not None:
            variation = self._variation_map[self._variation.strip().lower()]
        else:
            variation = random.choice(self._variations)

        # Render the system prompt with style instructions and selected variation
        system_prompt = self._system_prompt_template.render_template_value(
            style_instructions=self._style_instructions,
            variation=variation,
        )

        conversation_id = str(uuid.uuid4())

        self._converter_target.set_system_prompt(
            system_prompt=system_prompt,
            conversation_id=conversation_id,
            attack_identifier=None,
        )

        request = Message(
            [
                MessagePiece(
                    role="user",
                    original_value=prompt,
                    conversation_id=conversation_id,
                    sequence=1,
                    prompt_target_identifier=self._converter_target.get_identifier(),
                    original_value_data_type=input_type,
                    converted_value_data_type=input_type,
                    converter_identifiers=[self.get_identifier()],
                )
            ]
        )

        response = await self._converter_target.send_prompt_async(message=request)
        return ConverterResult(output_text=response[0].get_value(), output_type="text")

    @classmethod
    def list_available_filters(cls) -> list[str]:
        """
        List all available image filter names.

        Returns:
            List of filter names (YAML filenames without extension), excluding the system prompt.
        """
        return sorted(p.stem for p in IMAGE_FILTER_DIR.glob("*.yaml") if p.name != _SYSTEM_PROMPT_FILENAME)
