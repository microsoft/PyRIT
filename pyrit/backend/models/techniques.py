# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Technique catalog responses and runtime construction requests."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from pyrit.backend.models.common import PaginationInfo
from pyrit.models import Parameter
from pyrit.models.request_limits import MAX_ITEMS

TechniqueSelector = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]


class TechniqueInstance(BaseModel):
    """Safe display metadata for one registered factory."""

    name: str
    description: str | None = None
    attack_type: str
    tags: list[str] = Field(default_factory=list)
    uses_adversarial: bool
    uses_default_adversarial_target: bool
    creation_statement: str


class TechniqueListResponse(BaseModel):
    """A page of the active runtime catalog, without loading additional techniques."""

    items: list[TechniqueInstance]
    pagination: PaginationInfo


class TechniqueTypeEntry(BaseModel):
    """An existing attack class and its deferred construction inputs."""

    attack_type: str
    description: str
    parameters: list[Parameter]
    supports_adversarial: bool
    supports_converters: bool


class TechniqueTypeResponse(BaseModel):
    """Attack constructor metadata for the basic creation form."""

    items: list[TechniqueTypeEntry]


class CreateTechniqueRequest(BaseModel):
    """Construct a deferred factory using existing registry references."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    name: TechniqueSelector
    type: str = Field(min_length=1, max_length=256)
    params: dict[str, JsonValue] = Field(default_factory=dict, max_length=MAX_ITEMS)
    description: str | None = None
    tags: list[TechniqueSelector] = Field(default_factory=list, max_length=MAX_ITEMS)
    request_converters: list[str] | None = Field(default=None, max_length=MAX_ITEMS)
    response_converters: list[str] | None = Field(default=None, max_length=MAX_ITEMS)
    adversarial_chat: str | None = None
    adversarial_system_prompt: str | None = None
    adversarial_seed_prompt: str | None = None
    adversarial_prompt_template: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if value.casefold() in {"all", "default", "types"}:
            raise ValueError("all, default, and types are reserved technique names")
        return value

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        if len({tag.casefold() for tag in value}) != len(value):
            raise ValueError("Tags must be unique, including letter case")
        if {"all", "default"} & {tag.casefold() for tag in value}:
            raise ValueError("all and default are reserved selectors")
        return value
