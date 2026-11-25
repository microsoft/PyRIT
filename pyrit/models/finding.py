# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Any, NamedTuple, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.common import print_deprecation_message
from pyrit.models.decomposition import DecompositionNode, DecompositionPath, validate_own_decomposition
from pyrit.models.harm_category import HarmCategory

# TODO(PyRIT 1.5): Remove FindingSeverity, the PyRIT 1.2 finding fields, and Finding.severity.value.
_REMOVED_IN = "1.5.0"


class _FindingSeverity(str, Enum):
    """The PyRIT 1.2 severities, highest first. Deprecated: use the option keys of ``FindingFields.severity``."""

    CRITICAL = "critical"
    IMPORTANT = "important"
    MODERATE = "moderate"
    LOW = "low"
    INFORMATIONAL = "informational"
    OTHER = "other"


_SEVERITY_KEYS = frozenset(severity.value for severity in _FindingSeverity if severity is not _FindingSeverity.OTHER)
_HARM_CATEGORIES = frozenset(category.value for category in HarmCategory if category is not HarmCategory.OTHER)
# Each PyRIT 1.2 field, and what replaces it.
_DEPRECATED_FIELDS = {
    "description": "field_values={'description': ...}",
    "severity_other": "severity=...",
    "harm_type": "field_values={'harm_type': ...}",
    "harm_type_other": "field_values={'harm_type': ...}",
}


class _DeprecatedValues(NamedTuple):
    """The PyRIT 1.2 fields that a finding's values stand for."""

    description: str
    severity_other: str | None
    harm_type: HarmCategory | None
    harm_type_other: str | None


class _SeverityText(str):
    """A severity that still answers ``.value``, as a PyRIT 1.2 ``FindingSeverity`` did."""

    __slots__ = ()

    @property
    def value(self) -> str:
        """The severity as a plain string. Deprecated: use the severity itself."""
        print_deprecation_message(
            old_item="Finding.severity.value", new_item="Finding.severity", removed_in=_REMOVED_IN
        )
        return str(self)


def _resolve_other(*, name: str, selected: object, other: str, text: object) -> object:
    """
    Return what a PyRIT 1.2 selection and its Other text stand for: the text for Other, otherwise the selection.

    Text equal to the selection is also accepted, so a client can send back the values a response returned.

    Returns:
        object: The Other text, or the selection.

    Raises:
        ValueError: If Other has no text, or another selection has different text.
    """
    if selected == other:
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{name}_other must not be blank when {name} is Other.")
        return text
    if text is not None and text != selected:
        raise ValueError(f"{name}_other is only allowed when {name} is Other.")
    return selected


class FindingCreate(BaseModel):
    """
    A finding as an operator enters it, independent of attack outcomes and scores.

    A sub-finding names its parent finding. Every finding keeps its own severity, and ``demonstrated`` records
    whether the operator showed it, so a decomposition can include steps that were not achieved.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    severity: str = Field(description="The key of a severity option, or Other text; surrounding whitespace is removed")
    field_values: dict[str, str] = Field(
        {}, description="Values by field or level key, each an option's key or text; blank values are dropped"
    )
    parent_finding_id: UUID | None = None
    demonstrated: bool = True

    @model_validator(mode="before")
    @classmethod
    def _move_deprecated_fields(cls, data: Any) -> Any:
        """
        Move the PyRIT 1.2 fields where the upgrade migration moves saved values: Other severity text into
        ``severity``, and the harm type (its Other text for Other) and a non-blank description into ``field_values``.

        Returns:
            Any: The input, with the PyRIT 1.2 fields moved.

        Raises:
            ValueError: If Other text is missing or not allowed, as in PyRIT 1.2, or a moved value conflicts with
                ``field_values``.
        """
        if not isinstance(data, dict):
            return data
        # FindingSeverity.OTHER needs its text as in PyRIT 1.2, even without another PyRIT 1.2 field.
        if data.keys().isdisjoint(_DEPRECATED_FIELDS) and data.get("severity") is not _FindingSeverity.OTHER:
            return data
        data = dict(data)
        old = {name: data.pop(name) for name in _DEPRECATED_FIELDS if name in data}
        for name in old:
            print_deprecation_message(
                old_item=f"{cls.__name__}({name}=...)",
                new_item=f"{cls.__name__}({_DEPRECATED_FIELDS[name]})",
                removed_in=_REMOVED_IN,
            )
        if "severity" in data:
            data["severity"] = _resolve_other(
                name="severity", selected=data["severity"], other="other", text=old.get("severity_other")
            )
        moved = {
            "harm_type": _resolve_other(
                name="harm_type",
                selected=old.get("harm_type"),
                other=HarmCategory.OTHER,
                text=old.get("harm_type_other"),
            ),
            "description": old.get("description"),
        }
        field_values = data.get("field_values", {})
        if not isinstance(field_values, Mapping):
            return data
        field_values = dict(field_values)
        for key, value in moved.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if field_values.setdefault(key, value) != value:
                raise ValueError(f"{key} conflicts with field_values['{key}'].")
        data["field_values"] = field_values
        return data

    @property
    def description(self) -> str:
        """The description, or an empty string. Deprecated: read ``field_values.get("description", "")``."""
        print_deprecation_message(
            old_item=f"{type(self).__name__}.description",
            new_item=f"{type(self).__name__}.field_values.get('description', '')",
            removed_in=_REMOVED_IN,
        )
        return self._deprecated_values().description

    @property
    def severity_other(self) -> str | None:
        """The severity when it is not a PyRIT 1.2 severity, otherwise None. Deprecated: read ``severity``."""
        print_deprecation_message(
            old_item=f"{type(self).__name__}.severity_other",
            new_item=f"{type(self).__name__}.severity",
            removed_in=_REMOVED_IN,
        )
        return self._deprecated_values().severity_other

    @property
    def harm_type(self) -> HarmCategory | None:
        """The harm category, Other for typed text, or None. Deprecated: read ``field_values.get("harm_type")``."""
        print_deprecation_message(
            old_item=f"{type(self).__name__}.harm_type",
            new_item=f"{type(self).__name__}.field_values.get('harm_type')",
            removed_in=_REMOVED_IN,
        )
        return self._deprecated_values().harm_type

    @property
    def harm_type_other(self) -> str | None:
        """The typed harm type text, or None. Deprecated: read ``field_values.get("harm_type")``."""
        print_deprecation_message(
            old_item=f"{type(self).__name__}.harm_type_other",
            new_item=f"{type(self).__name__}.field_values.get('harm_type')",
            removed_in=_REMOVED_IN,
        )
        return self._deprecated_values().harm_type_other

    def _deprecated_values(self) -> _DeprecatedValues:
        """
        Read the PyRIT 1.2 fields that this finding's values stand for.

        Returns:
            _DeprecatedValues: The description, Other severity text, and harm type with its Other text.
        """
        harm = self.field_values.get("harm_type")
        is_category = harm in _HARM_CATEGORIES
        return _DeprecatedValues(
            description=self.field_values.get("description", ""),
            severity_other=None if self.severity in _SEVERITY_KEYS else self.severity,
            harm_type=None if harm is None else HarmCategory(harm) if is_category else HarmCategory.OTHER,
            harm_type_other=None if harm is None or is_category else harm,
        )

    @field_validator("title")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """
        Reject blank text without normalizing its contents.

        Returns:
            str: The original value.

        Raises:
            ValueError: If the value is blank.
        """
        if not value.strip():
            raise ValueError("Must not be blank.")
        return value

    @field_validator("severity")
    @classmethod
    def _strip_severity(cls, value: str) -> str:
        """
        Remove surrounding whitespace and reject blank text.

        Returns:
            str: The severity without surrounding whitespace.

        Raises:
            ValueError: If the severity is blank.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("Must not be blank.")
        return _SeverityText(stripped)

    @field_validator("field_values")
    @classmethod
    def _drop_blank_values(cls, value: dict[str, str]) -> dict[str, str]:
        """
        Drop empty and whitespace-only values, keeping other text exactly as entered.

        Returns:
            dict[str, str]: The non-blank values.
        """
        return {key: text for key, text in value.items() if text.strip()}


class Finding(FindingCreate):
    """
    A saved finding in one operation.

    Findings decompose like ``SeedObjective``s, without being linked to them: a parent lists ``paths``, each an
    ordered chain of its direct sub-findings that together demonstrate it. Paths are replaced on their own, so
    creating or updating a finding's fields never changes them.
    """

    operation_id: UUID
    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    version: int = Field(default=1, ge=1, description="Revision to supply when updating the finding's fields")
    paths: tuple[DecompositionPath, ...] = ()

    @property
    def decomposition_node(self) -> DecompositionNode:
        """The finding's decomposition relationships."""
        return DecompositionNode(id=self.id, parent_id=self.parent_finding_id, paths=self.paths)

    @model_validator(mode="after")
    def _validate_decomposition(self) -> Self:
        """
        Check the relationships the finding can validate without its relatives.

        Returns:
            Self: The validated finding.
        """
        validate_own_decomposition(node_id=self.id, parent_id=self.parent_finding_id, paths=self.paths)
        return self


def is_finding_path_complete(*, path: DecompositionPath, demonstrated: Mapping[UUID, bool]) -> bool:
    """
    Whether every step of a finding's path was demonstrated. Completeness is derived, never stored.

    Args:
        path (DecompositionPath): A path of direct sub-findings.
        demonstrated (Mapping[UUID, bool]): Each known sub-finding's demonstrated flag. Unknown steps count as
            not demonstrated.

    Returns:
        bool: True if each step is a demonstrated sub-finding.
    """
    return all(demonstrated.get(step, False) for step in path.steps)


def __getattr__(name: str) -> object:
    """
    Resolve the deprecated ``FindingSeverity``.

    Returns:
        object: The PyRIT 1.2 severity enum.

    Raises:
        AttributeError: For any other name.
    """
    if name != "FindingSeverity":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    print_deprecation_message(
        old_item=f"{__name__}.FindingSeverity",
        new_item="the option keys of FindingFields.severity",
        removed_in=_REMOVED_IN,
    )
    return _FindingSeverity


class FindingEvidence(BaseModel):
    """A saved attack result linked to a finding as evidence."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    finding_id: UUID
    attack_result_id: UUID
    attached_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
