# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Technique catalog responses and runtime construction requests."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from pyrit.backend.models.common import IdentifierStr, PaginationInfo, TextStr
from pyrit.models import ComponentIdentifier, Parameter, validate_registry_name
from pyrit.models.catalog import RegistryEntryOrigin
from pyrit.models.request_limits import MAX_ITEMS
from pyrit.models.technique_edit import TechniqueOverride, TechniqueSettings

TechniqueSelector = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]


class TechniqueInstance(BaseModel):
    """Safe display metadata for one registered factory."""

    name: str
    description: str | None = None
    attack_type: str
    tags: list[str] = Field(default_factory=list)
    uses_adversarial: bool
    uses_default_adversarial_target: bool
    identifier: ComponentIdentifier
    creation_statement: str
    origin: RegistryEntryOrigin | None = Field(
        None,
        description=(
            "Where the technique comes from: 'saved' (restored at every start), 'configuration' (the server's "
            "startup scripts), or 'temporary' (gone after a restart or reinitialization)"
        ),
    )
    saved_by: str | None = Field(
        None,
        description=(
            "Name of the user who saved the technique, for display only; None when it isn't saved or was saved "
            "with sign-in off"
        ),
    )
    saved_by_me: bool = Field(False, description="Whether the signed-in user asking is the one who saved the technique")


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


class TechniqueConfiguration(BaseModel):
    """Typed effective settings for an editor; opaque values remain on the source factory."""

    source_name: str
    source_identity: str
    attack_type: str
    settings: TechniqueSettings
    parameters: list[Parameter]
    editable_fields: list[str]
    supplied_parameters: list[str]
    read_only: dict[str, str]
    supports_prepended_conversation: bool = False
    supports_prompt: bool = False


class TechniqueEditResponse(BaseModel):
    """Validated local settings and a serializable replay specification; nothing is registered."""

    configuration: TechniqueConfiguration
    override: TechniqueOverride


class CreateTechniqueRequest(BaseModel):
    """Construct a deferred factory using existing registry references."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    name: TechniqueSelector
    persist: bool = Field(
        False,
        description=(
            "Save the technique so it comes back after the backend restarts or is reinitialized (true), or keep "
            "it only until then (false). A saved technique needs a name of lowercase letters, digits, and "
            "underscores, and can only use saved converters and targets or ones from the server's configuration."
        ),
    )
    type: str = Field(min_length=1, max_length=256)
    params: dict[IdentifierStr, JsonValue] = Field(default_factory=dict, max_length=MAX_ITEMS)
    description: TextStr | None = None
    tags: list[TechniqueSelector] = Field(default_factory=list, max_length=MAX_ITEMS)
    request_converters: list[IdentifierStr] | None = Field(default=None, max_length=MAX_ITEMS)
    response_converters: list[IdentifierStr] | None = Field(default=None, max_length=MAX_ITEMS)
    adversarial_chat: IdentifierStr | None = None
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

    @model_validator(mode="after")
    def _validate_persist(self) -> "CreateTechniqueRequest":
        if self.persist:
            validate_registry_name(self.name)
        return self
