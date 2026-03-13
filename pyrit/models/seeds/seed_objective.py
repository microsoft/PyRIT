# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedObjective class for representing seed objectives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Union

from pyrit.common.path import PATHS_DICT
from pyrit.models.seeds.seed import Seed

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SeedObjective(Seed):
    """Represents a seed objective with various attributes and metadata."""

    is_general_technique: bool = False

    def __post_init__(self) -> None:
        """
        Post-initialization to render the template to replace existing values.

        Raises:
            ValueError: If is_general_technique is True.
        """
        if self.is_general_technique:
            raise ValueError("SeedObjective cannot be a general technique.")
        self.value = super().render_template_value_silent(**PATHS_DICT)

    @classmethod
    def from_yaml_with_required_parameters(
        cls,
        template_path: Union[str, Path],
        required_parameters: list[str],
        error_message: Optional[str] = None,
    ) -> SeedObjective:
        """
        Load a Seed from a YAML file. Because SeedObjectives do not have any parameters, the required_parameters
        and error_message arguments are unused.

        Args:
            template_path: Path to the YAML file containing the template.
            required_parameters: List of parameter names that must exist in the template.
            error_message: Custom error message if validation fails. If None, a default message is used.

        Returns:
            SeedObjective: The loaded and validated seed of the specific subclass type.

        """
        return cls.from_yaml_file(template_path)
