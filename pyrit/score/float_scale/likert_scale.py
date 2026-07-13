# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.common import verify_and_resolve_path


@dataclass(frozen=True)
class LikertScaleEvalFiles:
    """Evaluation dataset files associated with a bundled Likert scale."""

    human_labeled_datasets_files: list[str]
    result_file: str
    harm_category: str | None = None


class LikertScaleEntry(BaseModel):
    """One score value and description in a Likert scale."""

    model_config = ConfigDict(frozen=True)

    score_value: int
    description: str

    @field_validator("score_value", mode="before")
    @classmethod
    def _validate_score_value(cls, value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("score_value must be a non-negative integer.")
        if isinstance(value, int):
            score_value = value
        elif isinstance(value, float):
            if not value.is_integer():
                raise ValueError("score_value must be a non-negative integer.")
            score_value = int(value)
        elif isinstance(value, str):
            try:
                score_value = int(value)
            except ValueError as exc:
                raise ValueError("score_value must be a non-negative integer.") from exc
            if str(score_value) != value.strip():
                raise ValueError("score_value must be a non-negative integer.")
        else:
            raise ValueError("score_value must be a non-negative integer.")
        if score_value < 0:
            raise ValueError("score_value must be a non-negative integer.")
        return score_value


class LikertScale(BaseModel):
    """A category and ordered entries defining a Likert scoring scale."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    category: str
    entries: tuple[LikertScaleEntry, ...] = Field(alias="scale_descriptions", min_length=1)
    evaluation_files: LikertScaleEvalFiles | None = Field(default=None, exclude=True)

    @field_validator("category")
    @classmethod
    def _validate_category(cls, category: str) -> str:
        if not category:
            raise ValueError("category must not be empty.")
        return category

    @model_validator(mode="after")
    def _validate_ordered_values(self) -> "LikertScale":
        values = [entry.score_value for entry in self.entries]
        if len(values) < 2 or any(current >= following for current, following in zip(values, values[1:], strict=False)):
            raise ValueError("Likert scale score values must be unique and strictly increasing.")
        return self

    @classmethod
    def from_yaml(
        cls,
        path: Path | str,
        *,
        evaluation_files: LikertScaleEvalFiles | None = None,
    ) -> "LikertScale":
        """
        Load a Likert scale from a YAML file.

        Args:
            path (Path | str): Path to the Likert scale YAML.
            evaluation_files (LikertScaleEvalFiles | None): Optional evaluation metadata to attach.

        Returns:
            LikertScale: The loaded scale.

        Raises:
            ValueError: If the YAML does not contain a mapping or fails model validation.
        """
        resolved_path = verify_and_resolve_path(path)
        loaded = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise ValueError(f"Likert scale YAML file '{resolved_path}' must contain a mapping.")
        scale = cls.model_validate(loaded)
        return scale.model_copy(update={"evaluation_files": evaluation_files})

    @property
    def minimum_value(self) -> int:
        """The lowest score value in the scale."""
        return self.entries[0].score_value

    @property
    def maximum_value(self) -> int:
        """The highest score value in the scale."""
        return self.entries[-1].score_value

    @property
    def render_params(self) -> dict[str, Any]:
        """The Jinja parameters used to render a Likert system prompt."""
        descriptions = "".join(f"'{entry.score_value}': {entry.description}\n" for entry in self.entries)
        return {
            "category": self.category,
            "likert_scale": descriptions,
            "min_scale_value": str(self.minimum_value),
            "max_scale_value": str(self.maximum_value),
        }
