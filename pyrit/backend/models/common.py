# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Common models for the PyRIT API.

Includes pagination, error handling (RFC 7807), shared base models, and request limits.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Self, cast

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.main import IncEx

from pyrit.models.request_limits import (
    MAX_IDENTIFIER_LENGTH,
    MAX_ITEMS,
    MAX_LABEL_KEY_LENGTH,
    MAX_LABEL_VALUE_LENGTH,
    MAX_TEXT_LENGTH,
)

REGISTRY_INSTANCE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

# Request limits. Identifier, list, label, and text limits are shared with ``pyrit.models``. Prompt
# content (message pieces, system prompts, preview input) and free-form values (metadata and
# parameter values) are only limited by the request body size, so long prompts and base64 media
# keep working.
MAX_CURSOR_LENGTH = 1_024
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


class RegistrationRequest(BaseModel):
    """A registration flag with compatible wire and Python access."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    register_instance: bool = Field(
        True, alias="register", description="Register the object; false returns only its descriptor"
    )

    @property
    def register(self) -> bool:
        """The registration flag, retained for Python caller compatibility."""
        return self.register_instance

    @register.setter
    def register(self, value: bool) -> None:
        self.register_instance = value

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """
        Copy the request, accepting the public registration key in updates.

        Returns:
            Self: The copied request.
        """
        if update is not None and "register" in update:
            update = dict(update)
            register = update.pop("register")
            if "register_instance" in update and update["register_instance"] != register:
                raise ValueError("register and register_instance updates must match")
            update["register_instance"] = register
        return super().model_copy(update=update, deep=deep)

    def model_dump(
        self, *, include: IncEx | None = None, exclude: IncEx | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """
        Serialize the request, accepting registration aliases in field filters.

        Returns:
            dict[str, Any]: The serialized request.
        """
        return super().model_dump(
            include=self._registration_filter(include), exclude=self._registration_filter(exclude), **kwargs
        )

    def model_dump_json(self, *, include: IncEx | None = None, exclude: IncEx | None = None, **kwargs: Any) -> str:
        """
        Serialize JSON, accepting registration aliases in field filters.

        Returns:
            str: The serialized request.
        """
        return super().model_dump_json(
            include=self._registration_filter(include), exclude=self._registration_filter(exclude), **kwargs
        )

    @staticmethod
    def _registration_filter(fields: IncEx | None) -> IncEx | None:
        if fields is None or "register" not in fields:
            return fields
        if isinstance(fields, set):
            return (cast("set[str]", fields) - {"register"}) | {"register_instance"}
        translated = dict(cast("Mapping[str, IncEx | bool]", fields))
        translated["register_instance"] = translated.pop("register")
        return translated


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
