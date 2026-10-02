# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Common models for the PyRIT API.

Includes pagination, error handling (RFC 7807), shared base models, and request limits.
"""

from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field

from pyrit.models.request_limits import MAX_IDENTIFIER_LENGTH, MAX_ITEMS, MAX_LABEL_KEY_LENGTH, MAX_LABEL_VALUE_LENGTH

REGISTRY_INSTANCE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

# Request limits. Identifier, list, and label limits are shared with ``pyrit.models``. Prompt
# content (message pieces, system prompts, preview input) and free-form values (metadata and
# parameter values) are only limited by the request body size, so long prompts and base64 media
# keep working.
MAX_CURSOR_LENGTH = 1_024
MAX_TEXT_LENGTH = 100_000
MAX_FILE_CONTENT_LENGTH = 1_048_576

IdentifierStr = Annotated[str, Field(max_length=MAX_IDENTIFIER_LENGTH)]
CursorStr = Annotated[str, Field(max_length=MAX_CURSOR_LENGTH)]
TextStr = Annotated[str, Field(max_length=MAX_TEXT_LENGTH)]
LabelDict = Annotated[
    dict[
        Annotated[str, Field(max_length=MAX_LABEL_KEY_LENGTH)],
        Annotated[str, Field(max_length=MAX_LABEL_VALUE_LENGTH)],
    ],
    Field(max_length=MAX_ITEMS),
]


def validate_label_filter(value: str) -> str:
    """
    Check that a label filter is ``key:value`` within the label key and value limits.

    Returns:
        str: The unchanged filter.

    Raises:
        ValueError: If the filter has no ``:`` separator or a part is too long.
    """
    key, separator, label_value = value.partition(":")
    if not separator:
        raise ValueError("Label filters must use the key:value format.")
    if len(key.strip()) > MAX_LABEL_KEY_LENGTH or len(label_value.strip()) > MAX_LABEL_VALUE_LENGTH:
        raise ValueError(
            f"Label filter keys are limited to {MAX_LABEL_KEY_LENGTH} characters "
            f"and values to {MAX_LABEL_VALUE_LENGTH} characters."
        )
    return value


LabelFilterStr = Annotated[str, AfterValidator(validate_label_filter)]


class PaginationInfo(BaseModel):
    """Pagination metadata for list responses."""

    limit: int = Field(..., description="Maximum items per page")
    has_more: bool = Field(..., description="Whether more items exist")
    next_cursor: str | None = Field(None, description="Cursor for next page")
    prev_cursor: str | None = Field(None, description="Cursor for previous page")


class FieldError(BaseModel):
    """Individual field validation error."""

    field: str = Field(..., description="Field name with path (e.g., 'pieces[0].data_type')")
    message: str = Field(..., description="Error message")
    code: str | None = Field(None, description="Error code")
    value: Any | None = Field(None, description="The invalid value")


class ProblemDetail(BaseModel):
    """
    RFC 7807 Problem Details response.

    Used for all error responses to provide consistent error formatting.
    """

    type: str = Field(..., description="Error type URI (e.g., '/errors/validation-error')")
    title: str = Field(..., description="Short human-readable summary")
    status: int = Field(..., description="HTTP status code")
    detail: str = Field(..., description="Human-readable explanation")
    instance: str | None = Field(None, description="URI of the specific occurrence")
    errors: list[FieldError] | None = Field(None, description="Field-level errors for validation")


# Sensitive field patterns to filter from identifiers
SENSITIVE_FIELD_PATTERNS = frozenset(
    [
        "api_key",
        "_api_key",
        "token",
        "secret",
        "password",
        "credential",
        "auth",
        "key",
    ]
)


def filter_sensitive_fields(data: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively filter sensitive fields from a dictionary.

    Args:
        data: Dictionary potentially containing sensitive fields.

    Returns:
        dict[str, Any]: Dictionary with sensitive fields removed.
    """
    if not isinstance(data, dict):
        return data

    filtered: dict[str, Any] = {}
    for key, value in data.items():
        # Check if key matches sensitive patterns
        key_lower = key.lower()
        is_sensitive = any(pattern in key_lower for pattern in SENSITIVE_FIELD_PATTERNS)

        if is_sensitive:
            continue

        # Recursively filter nested dicts
        if isinstance(value, dict):
            filtered[key] = filter_sensitive_fields(value)
        elif isinstance(value, list):
            filtered[key] = [filter_sensitive_fields(item) if isinstance(item, dict) else item for item in value]
        else:
            filtered[key] = value

    return filtered
