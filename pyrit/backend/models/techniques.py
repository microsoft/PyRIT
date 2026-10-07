# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Technique catalog responses and the shared construction contract."""

from typing import Any

from pydantic import BaseModel, Field

from pyrit.models import Parameter
from pyrit.models.technique_definition import TechniqueDefinition


class TechniqueInstance(BaseModel):
    """Safe display metadata for one registered factory."""

    name: str
    description: str | None = None
    attack_type: str
    tags: list[str] = Field(default_factory=list)
    uses_adversarial: bool
    uses_default_adversarial_target: bool
    configuration: dict[str, Any]


class TechniqueListResponse(BaseModel):
    """The active runtime catalog, without loading additional techniques."""

    items: list[TechniqueInstance]


class TechniqueTypeEntry(BaseModel):
    """An existing attack class and its deferred construction inputs."""

    attack_type: str
    description: str
    parameters: list[Parameter]
    supports_adversarial: bool
    supports_converters: bool


class TechniqueTypeResponse(BaseModel):
    """Metadata for forms and clients that use the full definition contract."""

    items: list[TechniqueTypeEntry]
    definition_schema: dict[str, Any]
    factory_parameters: list[Parameter]
    seed_parameters: dict[str, list[Parameter]]


CreateTechniqueRequest = TechniqueDefinition
