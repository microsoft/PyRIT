# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Module for loading and validating harm definition YAML files.

Harm definitions provide the scale descriptions used for evaluating harm categories.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import yaml

from pyrit.common.path import HARM_DEFINITION_PATH

logger = logging.getLogger(__name__)


@dataclass
class ScaleDescription:
    """
    A single scale description entry from a harm definition.

    Args:
        score_value: The score value (e.g., "1", "2", etc.)
        description: The description for this score level.

    """

    score_value: str
    description: str


@dataclass
class HarmDefinition:
    """
    A harm definition loaded from a YAML file.

    This class represents the structured content of a harm definition YAML file,
    which includes the version, category name, and scale descriptions that define
    how to score content for this harm category.

    Args:
        version: The version of the harm definition (e.g., "1.0").
        category: The harm category name (e.g., "violence", "hate_speech").
        scale_descriptions: List of scale descriptions defining score levels.
        source_path: The path to the YAML file this was loaded from.

    """

    version: str
    category: str
    scale_descriptions: list[ScaleDescription] = field(default_factory=list)
    source_path: Optional[str] = field(default=None, kw_only=True)

    def get_scale_description(self, score_value: str) -> Optional[str]:
        """
        Get the description for a specific score value.

        Args:
            score_value: The score value to look up (e.g., "1", "2").

        Returns:
            The description for the score value, or None if not found.

        """
        for scale in self.scale_descriptions:
            if scale.score_value == score_value:
                return scale.description
        return None

    @staticmethod
    def validate_category(category: str, *, check_exists: bool = False) -> bool:
        """
        Validate a harm category name.

        Validates that the category name follows the naming convention (lowercase letters
        and underscores only) and optionally checks if it exists in the standard
        harm definitions.

        Args:
            category: The category name to validate.
            check_exists: If True, also verify the category exists in
                get_all_harm_definitions(). Defaults to False.

        Returns:
            True if the category is valid (and exists if check_exists is True),
            False otherwise.

        """
        # Check if category matches pattern: only lowercase letters and underscores
        if not re.match(r"^[a-z_]+$", category):
            return False

        if check_exists:
            all_definitions = get_all_harm_definitions()
            if category not in all_definitions:
                return False

        return True

    @classmethod
    def from_yaml(cls, harm_definition_path: Union[str, Path]) -> "HarmDefinition":
        """
        Load and validate a harm definition from a YAML file.

        The function first checks if the path is a simple filename (e.g., "violence.yaml")
        and if so, looks for it in the standard HARM_DEFINITION_PATH directory.
        Otherwise, it treats the path as a full or relative path.

        Args:
            harm_definition_path: Path to the harm definition YAML file.
                Can be a simple filename like "violence.yaml" which will be resolved
                relative to the standard harm_definition directory, or a full path.

        Returns:
            HarmDefinition: The loaded harm definition.

        Raises:
            FileNotFoundError: If the harm definition file does not exist.
            ValueError: If the YAML file is invalid or missing required fields.

        """
        path = Path(harm_definition_path)

        # If it's just a filename (no directory separators), look in the standard directory
        resolved_path = HARM_DEFINITION_PATH / path if path.parent == Path(".") else path

        if not resolved_path.exists():
            raise FileNotFoundError(
                f"Harm definition file not found: {resolved_path}. "
                f"Expected a YAML file in {HARM_DEFINITION_PATH} or a valid path."
            )

        try:
            with open(resolved_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in harm definition file {resolved_path}: {e}") from e

        if not isinstance(data, dict):
            raise ValueError(f"Harm definition file {resolved_path} must contain a YAML mapping/dictionary.")

        # Validate required fields
        if "version" not in data:
            raise ValueError(f"Harm definition file {resolved_path} is missing required 'version' field.")
        if "category" not in data:
            raise ValueError(f"Harm definition file {resolved_path} is missing required 'category' field.")
        if "scale_descriptions" not in data:
            raise ValueError(f"Harm definition file {resolved_path} is missing required 'scale_descriptions' field.")

        # Parse scale descriptions
        scale_descriptions = []
        for item in data["scale_descriptions"]:
            if not isinstance(item, dict) or "score_value" not in item or "description" not in item:
                raise ValueError(
                    f"Each scale_description in {resolved_path} must have 'score_value' and 'description' fields."
                )
            scale_descriptions.append(
                ScaleDescription(
                    score_value=str(item["score_value"]),
                    description=str(item["description"]),
                )
            )

        return cls(
            version=str(data["version"]),
            category=str(data["category"]),
            scale_descriptions=scale_descriptions,
            source_path=str(resolved_path),
        )


def get_all_harm_definitions() -> dict[str, HarmDefinition]:
    """
    Load all harm definitions from the standard harm_definition directory.

    This function scans the HARM_DEFINITION_PATH directory for all YAML files
    and loads each one as a HarmDefinition.

    Returns:
        Dict[str, HarmDefinition]: A dictionary mapping category names to their
            HarmDefinition objects. The keys are the category names from the YAML files
            (e.g., "violence", "hate_speech").

    Raises:
        ValueError: If any YAML file in the directory is invalid.

    """
    harm_definitions: dict[str, HarmDefinition] = {}

    if not HARM_DEFINITION_PATH.exists():
        logger.warning(f"Harm definition directory does not exist: {HARM_DEFINITION_PATH}")
        return harm_definitions

    for yaml_file in HARM_DEFINITION_PATH.glob("*.yaml"):
        try:
            harm_def = HarmDefinition.from_yaml(yaml_file)
            harm_definitions[harm_def.category] = harm_def
        except (FileNotFoundError, ValueError) as e:
            logger.warning(f"Failed to load harm definition from {yaml_file}: {e}")

    return harm_definitions
