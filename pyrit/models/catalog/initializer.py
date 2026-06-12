# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer catalog models.

Initializers configure the PyRIT environment (targets, datasets, env vars)
before scenario execution. These models describe registered-initializer
metadata that both the backend and external REST clients (the CLI today)
consume from ``/api/initializers``.

Per-field documentation strings (``Field(..., description=...)``) deliberately
live in the backend layer rather than here — see ``pyrit.models.MessagePiece``
vs ``pyrit.backend.models.attacks.MessagePieceView`` for the same split.
"""

from pydantic import BaseModel, Field


class InitializerParameterSummary(BaseModel):
    """Summary of an initializer-declared parameter."""

    name: str
    description: str
    default: list[str] | None = None


class RegisteredInitializer(BaseModel):
    """Summary of a registered initializer."""

    initializer_name: str
    initializer_type: str
    description: str = ""
    required_env_vars: list[str] = Field(default_factory=list)
    supported_parameters: list[InitializerParameterSummary] = Field(default_factory=list)
