# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Common response models for the PyRIT API.

Includes pagination, error handling (RFC 7807), and shared base models.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class PaginationInfo(BaseModel):
    """Pagination metadata for list responses."""

    limit: int = Field(..., description="Maximum items per page")
    has_more: bool = Field(..., description="Whether more items exist")
    next_cursor: Optional[str] = Field(None, description="Cursor for next page")
    prev_cursor: Optional[str] = Field(None, description="Cursor for previous page")


class FieldError(BaseModel):
    """Individual field validation error."""

    field: str = Field(..., description="Field name with path (e.g., 'pieces[0].data_type')")
    message: str = Field(..., description="Error message")
    code: Optional[str] = Field(None, description="Error code")
    value: Optional[Any] = Field(None, description="The invalid value")


class ProblemDetail(BaseModel):
    """
    RFC 7807 Problem Details response.

    Used for all error responses to provide consistent error formatting.
    """

    type: str = Field(..., description="Error type URI (e.g., '/errors/validation-error')")
    title: str = Field(..., description="Short human-readable summary")
    status: int = Field(..., description="HTTP status code")
    detail: str = Field(..., description="Human-readable explanation")
    instance: Optional[str] = Field(None, description="URI of the specific occurrence")
    errors: Optional[list[FieldError]] = Field(None, description="Field-level errors for validation")


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
