# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from pyrit.common import verify_and_resolve_path


class NumericRange(BaseModel):
    """The numeric range and optional category used to normalize a float score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_value: int
    maximum_value: int
    category: str | None = None

    @model_validator(mode="after")
    def _validate_range(self) -> "NumericRange":
        if self.minimum_value >= self.maximum_value:
            raise ValueError("minimum_value must be less than maximum_value.")
        if self.category is not None and not self.category:
            raise ValueError("category must not be empty.")
        return self


class NumericRubric(NumericRange):
    """A configurable numeric scoring scale and its prompt-rendering parameters."""

    model_config = ConfigDict(extra="allow", frozen=True)

    category: str
    minimum_description: str | None = None
    maximum_description: str | None = None
    step_description: str | None = None
    examples: str | None = None

    @classmethod
    def from_yaml(cls, path: Path | str) -> "NumericRubric":
        """
        Load a scale and its template parameters from a YAML file.

        Args:
            path (Path | str): Path to the scale YAML.

        Returns:
            NumericRubric: The loaded rubric.

        Raises:
            ValueError: If the YAML does not contain a mapping or fails model validation.
        """
        resolved_path = verify_and_resolve_path(path)
        loaded = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise ValueError(f"Numeric rubric YAML file '{resolved_path}' must contain a mapping.")
        return cls.model_validate(loaded)

    @property
    def render_params(self) -> dict[str, Any]:
        """The Jinja parameters used to render a scale system prompt."""
        return {key: "" if value is None else value for key, value in self.model_dump().items()}
