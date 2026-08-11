# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Persisted additional initializers that run after the ``.pyrit_conf`` baseline."""

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from pyrit.models.identifiers import validate_registry_name


class AdditionalInitializer(BaseModel):
    """
    A user-added initializer that runs after the ``.pyrit_conf`` baseline, in stored order.

    Unlike the config baseline (which is fixed in ``.pyrit_conf``), additional initializers are
    persisted in Central Memory and appended to the startup sequence. Multiple rows may reference
    the same ``initializer_name``; each is its own invocation, identified by ``id``.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), description="Stable unique row id.")
    initializer_name: str = Field(..., description="Initializer registry name.")
    parameters: dict[str, Any] | None = Field(
        default=None,
        description="JSON-serializable parameters for this initializer invocation.",
    )
    order_index: int | None = Field(
        default=None,
        ge=0,
        description="Optional zero-based position among the additional initializers.",
    )

    @field_validator("initializer_name")
    @classmethod
    def _validate_initializer_name(cls, value: str) -> str:
        validate_registry_name(value)
        return value
