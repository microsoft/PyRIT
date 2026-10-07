# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""JSON-native definitions of reusable, deferred attack techniques."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from pyrit.models.request_limits import MAX_ITEMS

TechniqueSelector = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]


class TechniqueScorerOverridePolicy(str, Enum):
    """Behavior when the execution scorer has an incompatible type."""

    WARN = "warn"
    RAISE = "raise"
    SKIP = "skip"


class TechniqueFactoryOptions(BaseModel):
    """Settings captured by a factory, not execution inputs."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    adversarial_chat: str | None = None
    adversarial_system_prompt: str | dict[str, JsonValue] | None = None
    adversarial_seed_prompt: str | dict[str, JsonValue] | None = None
    adversarial_prompt_template: str | dict[str, JsonValue] | None = None
    uses_adversarial: bool | None = None
    supports_additional_request_converters: bool = False
    scorer_override_policy: TechniqueScorerOverridePolicy = TechniqueScorerOverridePolicy.WARN
    use_score_as_feedback: bool | None = None

    @field_validator("scorer_override_policy", mode="before")
    @classmethod
    def _parse_policy(cls, value: object) -> TechniqueScorerOverridePolicy:
        return TechniqueScorerOverridePolicy(value)


class TechniqueSeedInput(BaseModel):
    """An explicitly named seed variant with JSON-native parameters."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    type: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=MAX_ITEMS)


class TechniqueSeedGroupInput(BaseModel):
    """Typed technique seeds and their placement within execution seeds."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    seeds: list[TechniqueSeedInput] = Field(min_length=1, max_length=MAX_ITEMS)
    insertion_index: int | None = None
    prompt_placement: Literal["preserve", "prepend"] = "preserve"


class TechniqueDefinition(BaseModel):
    """A construction recipe; component lookup belongs to the registry."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    name: TechniqueSelector
    description: str | None = None
    tags: list[TechniqueSelector] = Field(default_factory=list, max_length=MAX_ITEMS)
    attack_type: str = Field(min_length=1, max_length=256)
    attack_args: dict[str, JsonValue] = Field(default_factory=dict, max_length=MAX_ITEMS)
    factory_options: TechniqueFactoryOptions = Field(default_factory=TechniqueFactoryOptions)
    seed_technique: TechniqueSeedGroupInput | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if value.casefold() in {"all", "default", "types"}:
            raise ValueError("all, default, and types are reserved technique names")
        return value

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        folded = [tag.casefold() for tag in value]
        if len(set(folded)) != len(folded):
            raise ValueError("Tags must be unique, including letter case")
        if {"all", "default"} & set(folded):
            raise ValueError("all and default are reserved selectors")
        return value
