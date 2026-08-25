# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Persisted user-defined initializer source code."""

from pydantic import BaseModel, Field, field_validator

from pyrit.models.identifiers import validate_registry_name


class CustomInitializer(BaseModel):
    """A user-defined initializer that can be restored into the runtime registry."""

    initializer_name: str = Field(..., description="Initializer registry name.")
    script_content: str = Field(..., min_length=1, description="Python source defining a PyRITInitializer subclass.")

    @field_validator("initializer_name")
    @classmethod
    def _validate_initializer_name(cls, value: str) -> str:
        validate_registry_name(value)
        return value
