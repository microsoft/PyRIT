# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer catalog models.

Initializers configure the PyRIT environment (targets, datasets, env vars)
before scenario execution. These models describe registered-initializer
metadata that both the backend and external REST clients (the CLI today)
consume from ``/api/initializers``.
"""

from pydantic import BaseModel, Field


class InitializerParameterSummary(BaseModel):
    """Summary of an initializer-declared parameter."""

    name: str = Field(..., description="Parameter name")
    description: str = Field(..., description="Human-readable description of the parameter")
    default: list[str] | None = Field(None, description="Default value(s), or None if required")


class RegisteredInitializer(BaseModel):
    """Summary of a registered initializer."""

    initializer_name: str = Field(..., description="Initializer registry name (e.g., 'target')")
    initializer_type: str = Field(..., description="Initializer class name (e.g., 'TargetInitializer')")
    description: str = Field("", description="Human-readable description of the initializer")
    required_env_vars: list[str] = Field(
        default_factory=list, description="Environment variables required by this initializer"
    )
    supported_parameters: list[InitializerParameterSummary] = Field(
        default_factory=list, description="Parameters accepted by this initializer"
    )
