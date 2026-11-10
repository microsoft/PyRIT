# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Adapted from responsibleai/ASSERT v0.3.0 (2026-09-04).
# Source revision: 04b713fe73879208369750b3bd5ad53544f3eecc.
# Source: docs/config/schema.md (pipeline.judge) and assert_ai/library/judges/README.md.

"""Custom ASSERT judge dimensions, reusable presets, and judge configuration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from pyrit.common.yaml_helper import safe_load_yaml

_Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
_Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)]
_PresetName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=100)]


class _AssertJudgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class AssertJudgeGrade(_AssertJudgeModel):
    """One declared grade of a graded judge dimension."""

    value: StrictInt | StrictStr
    label: _Text


class AssertJudgeScale(_AssertJudgeModel):
    """
    ASSERT's ordered grade scale for a judge dimension.

    Declaration order is grade order. ASSERT's mapping form (``values: {1: Poor, 2: Good}``) is
    accepted and converted to grades.
    """

    type: Literal["ordinal"] = "ordinal"
    values: tuple[AssertJudgeGrade, ...] = Field(min_length=2)

    @field_validator("values", mode="before")
    @classmethod
    def _accept_mapping(cls, values: Any) -> Any:
        if isinstance(values, Mapping):
            return tuple({"value": value, "label": label} for value, label in values.items())
        return values

    @model_validator(mode="after")
    def _validate_grades(self) -> Self:
        grades = self.grade_values
        if len(set(grades)) != len(grades):
            raise ValueError("Scale grade values must be unique.")
        if len({type(grade) for grade in grades}) != 1:
            raise ValueError("Scale grade values must all be integers or all be strings.")
        if any(isinstance(grade, str) and not grade.strip() for grade in grades):
            raise ValueError("Scale grade values must not be blank.")
        return self

    @property
    def grade_values(self) -> tuple[int | str, ...]:
        """The declared grade values, in order."""
        return tuple(grade.value for grade in self.values)


class AssertJudgeDimension(_AssertJudgeModel):
    """
    A custom ASSERT judge dimension.

    Without a ``scale`` the dimension is a boolean bad-event flag: True means the undesirable event
    occurred. With ASSERT's ordinal ``scale`` the judge returns one of the declared grades, and
    ``violation_at`` (a PyRIT addition) names the first grade, in declared order, that counts as a
    violation. ``allow_not_applicable`` lets the judge report that the conversation did not exercise
    the dimension; such a verdict is undetermined. The built-in ``policy_violation`` and
    ``overrefusal`` dimensions are judged against the taxonomy and cannot be redefined; disable one
    and add a differently named dimension to replace it.
    """

    BUILT_IN_NAMES: ClassVar[tuple[str, ...]] = ("policy_violation", "overrefusal")

    name: _Identifier
    description: _Text
    rubric: _Text
    allow_not_applicable: StrictBool = False
    scale: AssertJudgeScale | None = None
    violation_at: StrictInt | StrictStr | None = None

    @field_validator("name")
    @classmethod
    def _reject_built_in_name(cls, name: str) -> str:
        if name in cls.BUILT_IN_NAMES:
            raise ValueError(
                f"'{name}' is a built-in ASSERT judge dimension that PyRIT judges against the taxonomy, so it "
                "cannot be redefined. Built-ins are enabled by default; to replace one, disable it and add a "
                "differently named dimension."
            )
        return name

    @model_validator(mode="after")
    def _validate_violation_at(self) -> Self:
        if self.scale is None:
            if self.violation_at is not None:
                raise ValueError("violation_at is only valid with a scale.")
            return self
        grades = self.scale.grade_values
        if self.violation_at is None:
            raise ValueError(
                f"Dimension '{self.name}' has a scale but no violation_at. PyRIT judges every dimension as a "
                "violation or not, so name the first grade, in declared order, that counts as a violation. "
                "(ASSERT reports grade counts instead of a violation rate for scaled dimensions.)"
            )
        index = self.grade_index(self.violation_at)
        if index is None:
            raise ValueError(f"violation_at {self.violation_at!r} is not one of the declared grades {list(grades)}.")
        if index == 0:
            raise ValueError("violation_at can't be the first grade, because every grade would be a violation.")
        return self

    @property
    def grade_values(self) -> tuple[bool | int | str, ...]:
        """The verdicts the judge may return, in order: the scale's grades, or ``False`` then ``True``."""
        return self.scale.grade_values if self.scale is not None else (False, True)

    @property
    def violation_index(self) -> int:
        """The position of the first grade that counts as a violation."""
        if self.violation_at is None:
            return 1
        index = self.grade_index(self.violation_at)
        assert index is not None
        return index

    def grade_index(self, value: object) -> int | None:
        """
        Find a verdict's position among the dimension's grades, matching its type exactly.

        Args:
            value (object): A boolean verdict, or a declared grade.

        Returns:
            int | None: The position, or None if the value is not one of the grades.
        """
        return next(
            (i for i, grade in enumerate(self.grade_values) if type(grade) is type(value) and grade == value),
            None,
        )


class AssertJudgePreset(_AssertJudgeModel):
    """
    A named, reusable group of judge dimensions.

    The YAML form matches ASSERT's ``judge_preset`` files: ``dimensions`` maps each dimension
    name to its definition. ``builtin_dimensions`` names the built-in dimensions a preset covers.
    Built-ins are always enabled unless disabled, so listing them only documents the preset.
    """

    kind: Literal["judge_preset"] = "judge_preset"
    name: _PresetName
    version: str = "1.0"
    tags: tuple[str, ...] = ()
    description: str = ""
    builtin_dimensions: tuple[str, ...] = ()
    dimensions: tuple[AssertJudgeDimension, ...] = ()

    @field_validator("dimensions", mode="before")
    @classmethod
    def _accept_mapping(cls, dimensions: Any) -> Any:
        if isinstance(dimensions, Mapping):
            return tuple(
                {**definition, "name": name} if isinstance(definition, Mapping) else definition
                for name, definition in dimensions.items()
            )
        return dimensions

    @model_validator(mode="after")
    def _validate_dimensions(self) -> Self:
        unknown = sorted(set(self.builtin_dimensions) - set(AssertJudgeDimension.BUILT_IN_NAMES))
        if unknown:
            raise ValueError(f"Unknown built-in judge dimensions: {', '.join(unknown)}.")
        names = [dimension.name for dimension in self.dimensions]
        if len(set(names)) != len(names):
            raise ValueError("Judge preset dimension names must be unique.")
        if not names and not self.builtin_dimensions:
            raise ValueError("A judge preset must define at least one dimension.")
        return self

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> AssertJudgePreset:
        """
        Load a preset from an ASSERT-style ``judge_preset`` YAML file.

        Args:
            path (str | Path): The preset file.

        Returns:
            AssertJudgePreset: The validated preset.

        Raises:
            ValueError: If the file is not a YAML mapping or is not a valid judge preset.
        """
        loaded = safe_load_yaml(Path(path).read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise ValueError(f"Judge preset file {path} must contain a YAML mapping.")
        try:
            return cls.model_validate(dict(loaded))
        except ValidationError as error:
            raise ValueError(f"Invalid judge preset file {path}: {describe_validation_errors(error)}") from None


def describe_validation_errors(error: ValidationError) -> str:
    """
    Describe validation errors by field location and reason, without the rejected values.

    Values are omitted so that errors about externally supplied input can't echo file contents.

    Args:
        error (ValidationError): The validation error.

    Returns:
        str: One ``location: message`` entry per error, separated by semicolons.
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'value'}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False, include_context=False)
    )


class AssertJudgeConfig(_AssertJudgeModel):
    """
    The judge dimensions an ASSERT evaluation scores.

    Dimensions resolve in order: built-ins, then built-in ``presets`` by name, then ``preset_files``,
    then inline ``dimensions``. A later definition replaces an earlier one with the same name, and
    ``disabled_dimensions`` removes names from the result. ``samples`` repeats every judge call and
    aggregates the verdicts. ``objective_dimension`` is the boolean dimension a scenario uses as its
    objective; every other dimension is recorded as an auxiliary score.
    """

    DEFAULT_OBJECTIVE_DIMENSION: ClassVar[str] = "policy_violation"

    presets: tuple[_PresetName, ...] = ()
    preset_files: tuple[Path, ...] = ()
    dimensions: tuple[AssertJudgeDimension, ...] = ()
    disabled_dimensions: tuple[_Identifier, ...] = ()
    samples: Annotated[int, Field(strict=True, ge=1, le=25)] = 1
    objective_dimension: _Identifier = DEFAULT_OBJECTIVE_DIMENSION

    @model_validator(mode="after")
    def _validate_names(self) -> Self:
        names = [dimension.name for dimension in self.dimensions]
        if len(set(names)) != len(names):
            raise ValueError("Inline judge dimension names must be unique.")
        if len(set(self.disabled_dimensions)) != len(self.disabled_dimensions):
            raise ValueError("disabled_dimensions must not repeat a name.")
        if len(set(self.presets)) != len(self.presets):
            raise ValueError("presets must not repeat a preset.")
        if len(set(self.preset_files)) != len(self.preset_files):
            raise ValueError("preset_files must not repeat a file.")
        return self
