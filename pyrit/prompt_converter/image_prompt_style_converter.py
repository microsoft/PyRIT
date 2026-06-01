# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import random
from pathlib import Path
from typing import Any

import yaml

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.models import (
    ComponentIdentifier,
    PromptDataType,
    SeedPrompt,
)
from pyrit.prompt_converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.prompt_converter.prompt_converter import ConverterResult
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

IMAGE_PROMPT_STYLE_DIR = Path(CONVERTER_SEED_PROMPT_PATH) / "image_prompt_style"
SYSTEM_PROMPT_FILENAME = "image_prompt_style_system_prompt.yaml"


class ImagePromptStyleConverter(LLMGenericTextConverter):
    """
    LLM-based converter that expands a short objective into a detailed image generation prompt
    using a photographic style filter and scene variation.

    The converter loads a filter YAML file containing style_instructions and a list of variations,
    then uses an LLM to expand the user's objective into a fully styled image generation prompt.
    """

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        filter_name: str | None = None,
        filter_path: str | Path | None = None,
        variation: str | None = None,
    ) -> None:
        """
        Initialize the converter with a target LLM, filter specification, and optional variation.

        Exactly one of ``filter_name`` or ``filter_path`` may be provided.  If neither is given,
        a random built-in filter is selected.

        Args:
            converter_target: The LLM endpoint that generates the expanded prompt. Must satisfy
                ``CHAT_TARGET_REQUIREMENTS`` (inherited from ``LLMGenericTextConverter``).
                Can be omitted if a default has been configured via PyRIT initialization.
            filter_name: Name of a built-in filter YAML file (without extension) in the
                image_prompt_style directory.  Mutually exclusive with ``filter_path``.
            filter_path: Path to a custom filter YAML file.  Mutually exclusive with
                ``filter_name``.
            variation: Name of the variation to use (matched by key name in the YAML variations
                mapping, e.g. "wide_mirror_shot"). This is case-insensitive. If None, a random
                variation is selected on each call to convert_async.

        Raises:
            ValueError: If both filter_name and filter_path are provided.
            ValueError: If filter_name does not correspond to an existing YAML file.
            ValueError: If filter_path does not exist.
            ValueError: If variation does not match any entry in the filter.
        """
        if filter_name and filter_path:
            raise ValueError("Only one of 'filter_name' or 'filter_path' may be specified, not both.")

        self._variation = variation

        # Load the shared system prompt template
        system_prompt_path = IMAGE_PROMPT_STYLE_DIR / SYSTEM_PROMPT_FILENAME
        system_prompt_template = SeedPrompt.from_yaml_file(system_prompt_path)

        # Resolve the filter YAML file
        if filter_path is not None:
            resolved_path = Path(filter_path)
            if not resolved_path.exists():
                raise ValueError(f"Filter path '{filter_path}' does not exist.")
            self._filter_name = resolved_path.stem
        elif filter_name is not None:
            resolved_path = IMAGE_PROMPT_STYLE_DIR / f"{filter_name}.yaml"
            if not resolved_path.exists():
                available = self.list_available_filters()
                raise ValueError(f"Filter '{filter_name}' not found. Available filters: {available}")
            self._filter_name = filter_name
        else:
            # No filter specified — pick a random built-in filter
            available = self.list_available_filters()
            self._filter_name = random.choice(available)
            resolved_path = IMAGE_PROMPT_STYLE_DIR / f"{self._filter_name}.yaml"

        with open(resolved_path, encoding="utf-8") as f:
            filter_data = yaml.safe_load(f)
        self._validate_filter_data(filter_data, resolved_path)

        self._style_instructions: str = filter_data["style_instructions"]
        self._variations: dict[str, str] = filter_data["variations"]

        # Build a lookup map with lowercased keys for case-insensitive matching
        self._variation_map: dict[str, str] = {}
        for name in self._variations:
            key = name.strip().lower()
            if key in self._variation_map:
                logger.warning(
                    f"Duplicate variation key '{name}' in filter '{self._filter_name}', overwriting previous entry."
                )
            self._variation_map[key] = name

        if variation is not None:
            key = variation.strip().lower()
            if key not in self._variation_map:
                available_names = list(self._variations.keys())
                raise ValueError(
                    f"Variation '{variation}' not found in filter '{self._filter_name}'. "
                    f"Available variations: {available_names}"
                )

        # `style_instructions` is constant per-instance, so bake it into the template kwargs now.
        # `variation` may be random per-call, so it's set inside convert_async before delegating.
        super().__init__(
            converter_target=converter_target,
            system_prompt_template=system_prompt_template,
            style_instructions=self._style_instructions,
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
            prompt (str): The user's short objective
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult containing the expanded image generation prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        # Select variation (random per-call when not pinned at init)
        if self._variation is not None:
            name = self._variation_map[self._variation.strip().lower()]
        else:
            name = random.choice(list(self._variations.keys()))

        # Inject the per-call variation into the parent's system-prompt render kwargs
        self._prompt_kwargs["variation"] = f"{name}: {self._variations[name]}"

        return await super().convert_async(prompt=prompt, input_type=input_type)

    @staticmethod
    def _validate_filter_data(filter_data: Any, resolved_path: Path) -> None:
        """
        Validate the structure of a parsed filter YAML.

        Raises:
            ValueError: If the filter is malformed (not a mapping, missing required keys,
                wrong value types, or empty variations).
        """
        if not isinstance(filter_data, dict):
            raise ValueError(
                f"Filter '{resolved_path}' is malformed: expected a YAML mapping at the top level, "
                f"got {type(filter_data).__name__}."
            )
        if "style_instructions" not in filter_data:
            raise ValueError(f"Filter '{resolved_path}' is missing required key 'style_instructions'.")
        if "variations" not in filter_data:
            raise ValueError(f"Filter '{resolved_path}' is missing required key 'variations'.")
        if not isinstance(filter_data["style_instructions"], str):
            raise ValueError(
                f"Filter '{resolved_path}' key 'style_instructions' must be a string, "
                f"got {type(filter_data['style_instructions']).__name__}."
            )
        if not isinstance(filter_data["variations"], dict) or not filter_data["variations"]:
            raise ValueError(
                f"Filter '{resolved_path}' key 'variations' must be a non-empty mapping of "
                f"variation name → description."
            )

    @classmethod
    def list_available_filters(cls) -> list[str]:
        """
        List all available image filter names.

        Returns:
            List of filter names (YAML filenames without extension), excluding the system prompt.
        """
        return sorted(p.stem for p in IMAGE_PROMPT_STYLE_DIR.glob("*.yaml") if p.name != SYSTEM_PROMPT_FILENAME)

    @classmethod
    def list_available_variations(cls, *, filter_name: str) -> list[str]:
        """
        List all available variation names for a given filter.

        Args:
            filter_name: Name of a built-in filter YAML file (without extension).

        Returns:
            Sorted list of variation key names defined in the filter.

        Raises:
            ValueError: If filter_name does not correspond to an existing YAML file, or if the
                filter file is malformed.
        """
        resolved_path = IMAGE_PROMPT_STYLE_DIR / f"{filter_name}.yaml"
        if not resolved_path.exists():
            available = cls.list_available_filters()
            raise ValueError(f"Filter '{filter_name}' not found. Available filters: {available}")

        with open(resolved_path, encoding="utf-8") as f:
            filter_data = yaml.safe_load(f)
        cls._validate_filter_data(filter_data, resolved_path)

        return sorted(filter_data["variations"].keys())
