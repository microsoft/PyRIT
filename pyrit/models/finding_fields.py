# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
from collections.abc import Iterable, Iterator
from enum import StrEnum
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from pyrit.models.harm_category import HarmCategory


class FindingFieldType(StrEnum):
    """How the finding dialog collects a field's value."""

    TEXT = "text"
    MULTILINE = "multiline"
    CHOICE = "choice"


class FindingSeverityColor(StrEnum):
    """Badge color of a severity option. CoPyRIT renders each one with a Fluent UI badge color."""

    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    GRAY = "gray"
    DARK = "dark"
    NEUTRAL = "neutral"


class FindingValuesError(ValueError):
    """A finding's values do not fit the deployment's finding fields."""


class FindingFieldOption(BaseModel):
    """One option of a choice field, with the options of the next dropdown under it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    children: list["FindingFieldOption"] = []


class FindingField(BaseModel):
    """
    One field of the finding dialog, as a deployment defines it.

    Attributes:
        key (str): The stored name of the value. Keep it fixed; change ``label`` to rename what users see.
        label (str): The name the finding dialog and cards show.
        type (FindingFieldType): ``text``, ``multiline`` or ``choice``.
        required (bool): Whether every finding needs a value. For a cascade, every level with options needs one.
        options (list[FindingFieldOption]): A ``choice`` field's options. Write ``(key, label)`` pairs for one
            dropdown, or a dict from each pair to its children for a cascade.
        levels (list[tuple[str, str]]): The ``(key, label)`` of each dropdown under a cascade's first one.
            Each level's value is stored under its own key.
        default (str | None): A top-level option the finding dialog preselects for new findings.
        allow_other (bool): Whether a single-level ``choice`` field also accepts typed text.
        colors (dict[str, FindingSeverityColor]): The severity slot's badge color for each top-level option.
        hint (str | None): Help text under the field in the finding dialog.
        section (str | None): A heading the finding dialog shows where the section changes.
        internal (bool): Whether the field and its levels stay out of the Word and Markdown exports. The dashboard
            still shows them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    _KEY_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[a-z0-9_]+")

    key: str
    label: str
    type: FindingFieldType
    required: bool = False
    options: list[FindingFieldOption] = []
    levels: list[tuple[str, str]] = []
    default: str | None = None
    allow_other: bool = False
    colors: dict[str, FindingSeverityColor] = {}
    hint: str | None = None
    section: str | None = None
    internal: bool = False

    @field_validator("options", mode="before")
    @classmethod
    def _normalize_options(cls, value: Any) -> Any:
        """
        Turn ``(key, label)`` pairs and cascade dicts into option dicts.

        Returns:
            Any: The options in a form pydantic validates as ``FindingFieldOption`` objects.
        """
        return _parse_options(value)

    @model_validator(mode="after")
    def _validate_definition(self) -> Self:
        """
        Check keys, labels and the settings that the field's type allows.

        Returns:
            Self: The validated field.
        """
        self._check_key(self.key)
        self._check_not_blank()
        for level_key, _ in self.levels:
            self._check_key(level_key)
        if self.type == FindingFieldType.CHOICE:
            self._check_choice()
        else:
            self._check_no_choice_settings()
        return self

    def _check_key(self, key: str) -> None:
        """
        Require a key of lowercase letters, digits and underscores, other than ``__proto__``.

        Raises:
            ValueError: If the key uses any other character, is empty, or is ``__proto__``.
        """
        if not self._KEY_PATTERN.fullmatch(key):
            raise ValueError(f"Key '{key}' must use only lowercase letters, digits and underscores.")
        # JavaScript objects treat this name as their prototype, so CoPyRIT couldn't keep a value under it.
        if key == "__proto__":
            raise ValueError("Key '__proto__' is reserved.")

    def _check_not_blank(self) -> None:
        """
        Require non-blank labels, option keys and option labels.

        Raises:
            ValueError: If any of them is blank.
        """
        texts = [self.label, *(label for _, label in self.levels)]
        for option in _walk(self.options):
            texts += [option.key, option.label]
        if any(not text.strip() for text in texts):
            raise ValueError(f"Labels, option keys and option labels of '{self.key}' must not be blank.")

    def _check_no_choice_settings(self) -> None:
        """
        Reject the settings that only a choice field can use.

        Raises:
            ValueError: If the field sets options, levels, default, allow_other or colors.
        """
        settings = {
            "options": bool(self.options),
            "levels": bool(self.levels),
            "default": self.default is not None,
            "allow_other": self.allow_other,
            "colors": bool(self.colors),
        }
        for name, is_set in settings.items():
            if is_set:
                raise ValueError(f"Field '{self.key}' is a {self.type} field and cannot have {name}.")

    def _check_choice(self) -> None:
        """
        Check the option tree of a choice field and the settings that refer to it.

        Raises:
            ValueError: If the options are missing, repeat a sibling key, nest to a depth that
                ``levels`` does not match, or ``allow_other`` is combined with levels.
        """
        if not self.options:
            raise ValueError(f"Choice field '{self.key}' needs at least one option.")
        self._check_unique_siblings()
        depth = _depth(self.options)
        if depth != len(self.levels) + 1:
            raise ValueError(
                f"Field '{self.key}' needs one entry in levels for each dropdown below the field; "
                f"its options imply {depth - 1}, but levels has {len(self.levels)}."
            )
        self._check_top_level_references()
        if self.allow_other and self.levels:
            raise ValueError(f"Field '{self.key}' can take Other text only without levels.")

    def _check_unique_siblings(self) -> None:
        """
        Require unique option keys among the options of each dropdown.

        Raises:
            ValueError: If two sibling options share a key.
        """
        for siblings in [self.options, *(option.children for option in _walk(self.options))]:
            repeated = _first_repeated(option.key for option in siblings)
            if repeated is not None:
                raise ValueError(f"Field '{self.key}' repeats the option key '{repeated}' among sibling options.")

    def _check_top_level_references(self) -> None:
        """
        Require ``default`` and every ``colors`` key to name a top-level option.

        Raises:
            ValueError: If either names anything else.
        """
        top_level = {option.key for option in self.options}
        defaults = [] if self.default is None else [self.default]
        for key in [*defaults, *self.colors]:
            if key not in top_level:
                raise ValueError(f"'{key}' is not a top-level option of '{self.key}'.")


class FindingValueRow(BaseModel):
    """One filled value of a finding, labelled for display."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    value: str
    multiline: bool
    internal: bool


class FindingValueDescription(BaseModel):
    """A finding's values labelled for display, with the stored values that no field uses."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    severity_label: str
    rows: list[FindingValueRow]
    retired: list[tuple[str, str]]


class FindingFields(BaseModel):
    """A deployment's finding fields: the severity slot and the fields shown after it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: FindingField
    fields: list[FindingField] = []

    @property
    def severity_order(self) -> list[str]:
        """The top-level option keys of the severity slot, from highest to lowest severity."""
        return [option.key for option in self.severity.options]

    @property
    def field_keys(self) -> frozenset[str]:
        """The key of every field and level, except ``severity``."""
        keys = (key for field in [self.severity, *self.fields] for key, _ in _keys_and_labels(field))
        return frozenset(keys) - {"severity"}

    @field_validator("severity")
    @classmethod
    def _validate_severity_slot(cls, value: FindingField) -> FindingField:
        """
        Check the severity slot and mark it required.

        Returns:
            FindingField: The slot with ``required`` set to True.

        Raises:
            ValueError: If the slot's key is not ``severity``, it is not a choice field, it sets
                ``required=False``, or it is internal.
        """
        if value.key != "severity":
            raise ValueError(f"The severity slot's key must be 'severity', not '{value.key}'.")
        if value.type != FindingFieldType.CHOICE:
            raise ValueError("The severity slot must be a choice field.")
        if "required" in value.model_fields_set and not value.required:
            raise ValueError("The severity slot is always required; leave required out or set it to True.")
        if value.internal:
            raise ValueError("The severity slot can't be internal.")
        return value.model_copy(update={"required": True})

    @model_validator(mode="after")
    def _validate_keys(self) -> Self:
        """
        Check colors and keys across the severity slot, the fields and their levels.

        Returns:
            Self: The validated fields.

        Raises:
            ValueError: If a field other than severity sets colors, a key is used more than once,
                or a key is ``title``.
        """
        colored = next((field.key for field in self.fields if field.colors), None)
        if colored is not None:
            raise ValueError(f"Only the severity field can set colors, not '{colored}'.")
        keys = [key for field in [self.severity, *self.fields] for key, _ in _keys_and_labels(field)]
        repeated = _first_repeated(keys)
        if repeated is not None:
            raise ValueError(f"The key '{repeated}' is used more than once across fields and levels.")
        if "title" in keys:
            raise ValueError("The key 'title' is reserved for the finding title.")
        return self

    def check_values(
        self,
        *,
        severity: str,
        field_values: dict[str, str],
        previous_severity: str | None = None,
        previous_values: dict[str, str] | None = None,
    ) -> None:
        """
        Check a finding's severity and field values against these fields.

        On an edit, pass the stored finding's severity and values. A value that has not changed
        since then may name an option or key that no longer exists.

        Args:
            severity (str): The severity slot's value.
            field_values (dict[str, str]): The values of the other fields and of every level, by key.
            previous_severity (str | None): The stored finding's severity, or None for a new finding.
            previous_values (dict[str, str] | None): The stored finding's field values, or None for a
                new finding.

        Raises:
            FindingValuesError: For the first unknown key, changed value of a field that is no longer
                configured, missing required value, value that is not an option, or Other text that
                matches an option.
        """
        previous_values = previous_values or {}
        self._check_known_keys(field_values=field_values, previous_values=previous_values)
        values = {**field_values, "severity": severity}
        previous = {**previous_values, "severity": previous_severity or ""}
        for field in [self.severity, *self.fields]:
            _check_field_values(field=field, values=values, previous_values=previous)

    def describe_values(self, *, severity: str, field_values: dict[str, str]) -> FindingValueDescription:
        """
        Label a finding's filled values for display.

        Rows follow the configured order: the severity slot's levels, then each field and its levels.
        A choice value shows the label of the matching option under its stored parent; any other value
        shows as typed.

        Args:
            severity (str): The severity slot's value.
            field_values (dict[str, str]): The values of the other fields and of every level, by key.

        Returns:
            FindingValueDescription: The severity's label, one row per filled value, and the
                ``(key, value)`` of each stored value that no field or level uses, in stored order.
        """
        values = {**field_values, "severity": severity}
        rows = [row for field in [self.severity, *self.fields] for row in _value_rows(field=field, values=values)]
        severity_option = _find_option(options=self.severity.options, value=severity)
        known = self.field_keys
        return FindingValueDescription(
            severity_label=severity_option.label if severity_option is not None else severity,
            rows=[row for row in rows if row.key != "severity"],
            retired=[(key, value) for key, value in field_values.items() if key not in known],
        )

    def _check_known_keys(self, *, field_values: dict[str, str], previous_values: dict[str, str]) -> None:
        """
        Reject a key that no field or level uses, unless its value has not changed.

        Raises:
            FindingValuesError: If a key is unknown and its value differs from the stored one.
        """
        known = self.field_keys
        for key, value in field_values.items():
            if key in known or previous_values.get(key) == value:
                continue
            if key in previous_values:
                raise FindingValuesError(
                    f"Finding field {key!r} is no longer configured, so its saved value can't change."
                )
            raise FindingValuesError(f"Unknown finding field {key!r}.")


def _parse_options(value: Any) -> Any:
    """
    Turn authoring shorthand for options into a list of option dicts.

    A dict maps each ``(key, label)`` pair to its children, written the same way. In a list, a
    ``(key, label)`` pair becomes an option without children, and anything else passes through.

    Returns:
        Any: The options as a list, or ``value`` unchanged if it is neither a dict nor a list.
    """
    if isinstance(value, dict):
        return [_parse_branch(pair=pair, children=children) for pair, children in value.items()]
    if isinstance(value, list):
        return [{"key": item[0], "label": item[1]} if _is_pair(item) else item for item in value]
    return value


def _parse_branch(*, pair: Any, children: Any) -> dict[str, Any]:
    """
    Turn one cascade dict entry into an option dict with its children.

    Returns:
        dict[str, Any]: The option with its parsed children.

    Raises:
        ValueError: If ``pair`` is not a ``(key, label)`` pair.
    """
    if not _is_pair(pair):
        raise ValueError(f"Option {pair!r} must be a (key, label) pair.")
    return {"key": pair[0], "label": pair[1], "children": _parse_options(children)}


def _is_pair(value: Any) -> bool:
    """
    Check whether a value is a 2-item tuple or list of strings.

    Returns:
        bool: True if the value is a ``(key, label)`` pair.
    """
    return isinstance(value, tuple | list) and len(value) == 2 and all(isinstance(part, str) for part in value)


def _walk(options: list[FindingFieldOption]) -> Iterator[FindingFieldOption]:
    """
    Yield every option in a tree, each parent before its children.

    Yields:
        FindingFieldOption: Each option in the tree.
    """
    for option in options:
        yield option
        yield from _walk(option.children)


def _depth(options: list[FindingFieldOption]) -> int:
    """
    Count the dropdowns that the deepest branch of an option tree needs.

    Returns:
        int: The depth of the tree, or 0 for no options.
    """
    if not options:
        return 0
    return 1 + max(_depth(option.children) for option in options)


def _first_repeated(keys: Iterable[str]) -> str | None:
    """
    Find the first key that appears a second time.

    Returns:
        str | None: The first repeated key, or None if all keys are unique.
    """
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            return key
        seen.add(key)
    return None


def _keys_and_labels(field: FindingField) -> list[tuple[str, str]]:
    """
    List the ``(key, label)`` of a field followed by those of its levels.

    Returns:
        list[tuple[str, str]]: The field's and its levels' keys and labels in written order.
    """
    return [(field.key, field.label), *field.levels]


def _walk_values(
    *, field: FindingField, values: dict[str, str]
) -> Iterator[tuple[str, str, str | None, list[FindingFieldOption], FindingFieldOption | None]]:
    """
    Walk a field and its levels, following each value to the options of the next level.

    Yields:
        tuple[str, str, str | None, list[FindingFieldOption], FindingFieldOption | None]: The key, label,
            non-blank value or None, offered options, and matching option or None of the field and of each level.
    """
    options = field.options
    for key, label in _keys_and_labels(field):
        value = _non_blank(values.get(key))
        match = _find_option(options=options, value=value)
        yield key, label, value, options, match
        options = match.children if match is not None else []


def _check_field_values(*, field: FindingField, values: dict[str, str], previous_values: dict[str, str]) -> None:
    """
    Check the value of one field and of its levels.

    Raises:
        FindingValuesError: If a required value is missing or a value is not an option.
    """
    if field.type == FindingFieldType.CHOICE:
        _check_choice_values(field=field, values=values, previous_values=previous_values)
    elif field.required and _non_blank(values.get(field.key)) is None:
        raise FindingValuesError(f"{field.label} is required.")


def _check_choice_values(*, field: FindingField, values: dict[str, str], previous_values: dict[str, str]) -> None:
    """
    Walk a choice field's dropdowns, checking each value against the options under its parent.

    A value that is not an option passes if it and every value above it are unchanged.

    Raises:
        FindingValuesError: If a required dropdown with options has no value, or a changed value is
            not an option and is not allowed as Other text.
    """
    path_unchanged = True
    for key, label, value, options, match in _walk_values(field=field, values=values):
        path_unchanged = path_unchanged and value == _non_blank(previous_values.get(key))
        if value is None:
            if field.required and options:
                raise FindingValuesError(f"{label} is required.")
        elif match is None and not path_unchanged:
            _check_other_text(label=label, value=value, options=options, allow_other=field.allow_other)


def _check_other_text(*, label: str, value: str, options: list[FindingFieldOption], allow_other: bool) -> None:
    """
    Accept a value that is not an option only as Other text that names no option.

    Raises:
        FindingValuesError: If the field takes no Other text, or the text matches an option's key or
            label apart from case and surrounding spaces.
    """
    if not allow_other:
        raise FindingValuesError(f"{label} has no option {value!r}.")
    folded = value.strip().casefold()
    for option in options:
        if folded in (option.key.strip().casefold(), option.label.strip().casefold()):
            raise FindingValuesError(
                f"{label} {value!r} matches the option {option.label!r} (key {option.key!r}). "
                "Choose that option instead."
            )


def _non_blank(value: str | None) -> str | None:
    """
    Treat a blank value as missing.

    Args:
        value (str | None): The value, or None if it is missing.

    Returns:
        str | None: The value, or None if it is missing or blank.
    """
    return value if value and value.strip() else None


def _find_option(*, options: list[FindingFieldOption], value: str | None) -> FindingFieldOption | None:
    """
    Find the option whose key is a dropdown's value.

    Returns:
        FindingFieldOption | None: The matching option, or None if no option has that key.
    """
    return next((option for option in options if option.key == value), None)


def _value_rows(*, field: FindingField, values: dict[str, str]) -> Iterator[FindingValueRow]:
    """
    Label the filled values of one field and of its levels.

    Yields:
        FindingValueRow: A row for each filled value, in level order.
    """
    for key, label, value, _, match in _walk_values(field=field, values=values):
        if value is not None:
            yield FindingValueRow(
                key=key,
                label=label,
                value=match.label if match is not None else value,
                multiline=field.type == FindingFieldType.MULTILINE,
                internal=field.internal,
            )


DEFAULT_FINDING_FIELDS = FindingFields(
    severity=FindingField(
        key="severity",
        label="Severity",
        type=FindingFieldType.CHOICE,
        options=[
            ("critical", "Critical"),
            ("important", "Important"),
            ("moderate", "Moderate"),
            ("low", "Low"),
            ("informational", "Informational"),
        ],
        default="moderate",
        allow_other=True,
        colors={
            "critical": FindingSeverityColor.RED,
            "important": FindingSeverityColor.YELLOW,
            "moderate": FindingSeverityColor.YELLOW,
            "low": FindingSeverityColor.GRAY,
        },
    ),
    fields=[
        FindingField(
            key="harm_type",
            label="Harm-type",
            type=FindingFieldType.CHOICE,
            options=[
                (category.value, category.value) for category in HarmCategory if category is not HarmCategory.OTHER
            ],
            allow_other=True,
            hint="Optional. Select a category, or choose Other for custom text.",
        ),
        FindingField(key="description", label="Description", type=FindingFieldType.MULTILINE, hint="Optional"),
    ],
)
