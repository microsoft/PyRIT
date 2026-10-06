# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

from pyrit.common import verify_and_resolve_path


def _reject_bool(value: Any) -> Any:
    """
    Reject a boolean bound, which ``int`` validation would otherwise coerce to 0 or 1.

    ``bool`` subclasses ``int``, so a ``true`` bound in a rubric YAML would be silently
    reinterpreted as a number and shift or collapse the scale.

    Args:
        value (Any): The incoming bound.

    Returns:
        Any: The value unchanged when it is not a bool.

    Raises:
        ValueError: If the value is a bool.
    """
    if isinstance(value, bool):
        raise ValueError(f"scale bound must be an integer, not a bool ({value}).")
    return value


#: An integer scale bound that refuses ``bool``.
ScaleBound = Annotated[int, BeforeValidator(_reject_bool)]


class NumericRange(BaseModel):
    """The numeric range and optional category used to normalize a float score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_value: ScaleBound
    maximum_value: ScaleBound
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
