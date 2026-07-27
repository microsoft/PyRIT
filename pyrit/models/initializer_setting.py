# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Persisted initializer override settings."""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from pyrit.models.identifiers import validate_registry_name


class InitializerSetting(BaseModel):
    """Saved override for a registered initializer."""

    initializer_name: str = Field(..., description="Initializer registry name.")
    parameters: dict[str, Any] | None = Field(
        default=None,
        description="JSON-serializable parameter overrides for this initializer.",
    )
    order_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional zero-based startup-order override.",
    )

    @field_validator("initializer_name")
    @classmethod
    def _validate_initializer_name(cls, value: str) -> str:
        validate_registry_name(value)
        return value
