# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Typed, serializable text comparison criteria."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _TextMatcher(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    value: str
    case_sensitive: bool = False
    ignore_whitespace: bool = True


class Equals(_TextMatcher):
    """Compare the complete normalized text."""

    matcher_type: Literal["equals"] = "equals"


class Contains(_TextMatcher):
    """Find the normalized value within nonempty text."""

    matcher_type: Literal["contains"] = "contains"


class Regex(_TextMatcher):
    """Search text with the pattern as authored, without normalizing the pattern."""

    matcher_type: Literal["regex"] = "regex"

    @field_validator("value")
    @classmethod
    def _validate_pattern(cls, value: str) -> str:
        """
        Validate an authored regular expression.

        Returns:
            str: The unchanged pattern.

        Raises:
            ValueError: If the pattern is invalid.
        """
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"Invalid regular expression: {error}") from error
        return value


TextMatcher = Annotated[Equals | Contains | Regex, Field(discriminator="matcher_type")]
