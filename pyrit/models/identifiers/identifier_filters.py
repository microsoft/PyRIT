# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from pyrit.common.text_search import validate_text_search


class IdentifierType(Enum):
    """Enumeration of supported identifier types for filtering."""

    ATTACK = "attack"
    TARGET = "target"
    SCORER = "scorer"
    CONVERTER = "converter"


class IdentifierFilter(BaseModel):
    """
    Immutable filter definition for matching JSON-backed identifier properties.

    Attributes:
        identifier_type: The type of identifier column to filter on.
        property_path: The JSON path for the property to match.
        array_element_path : An optional JSON path that indicates the property at property_path is an array
            and the condition should resolve if the value at array_element_path matches the target
            for any element in that array. Cannot be used with partial_match or case_sensitive.
        value: The string value that must match the extracted JSON property value.
        value_pattern: An explicit case-insensitive full LIKE pattern, instead of value.
            '%' and '_' are wildcards, '/' escapes the next character; brackets and '*' are literal.
        partial_match: Whether value is a case-insensitive literal substring.
            Cannot be used with array_element_path, case_sensitive or value_pattern.
        case_sensitive: Whether the match should be case-sensitive.
            Cannot be used with array_element_path or partial_match.
    """

    model_config = ConfigDict(frozen=True)

    identifier_type: IdentifierType
    property_path: str
    value: str | None = None
    value_pattern: str | None = None
    array_element_path: str | None = None
    partial_match: bool = False
    case_sensitive: bool = False

    @model_validator(mode="after")
    def _validate_combinations(self) -> "IdentifierFilter":
        if (self.value is None) == (self.value_pattern is None):
            raise ValueError("Exactly one of value or value_pattern must be supplied")
        validate_text_search(value=self.value, pattern=self.value_pattern)
        if self.value_pattern is not None and (self.partial_match or self.case_sensitive or self.array_element_path):
            raise ValueError("Cannot use value_pattern with partial_match, case_sensitive or array_element_path")
        if self.array_element_path and (self.partial_match or self.case_sensitive):
            raise ValueError("Cannot use array_element_path with partial_match or case_sensitive")
        if self.partial_match and self.case_sensitive:
            raise ValueError("case_sensitive is not reliably supported with partial_match across all backends")
        return self
