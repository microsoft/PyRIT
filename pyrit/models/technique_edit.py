# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Serializable, run-local edits to an existing technique, not factory constructors."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SerializerFunctionWrapHandler, model_serializer

from pyrit.models.request_limits import MAX_IDENTIFIER_LENGTH, MAX_ITEMS

_Name = Annotated[str, Field(min_length=1, max_length=MAX_IDENTIFIER_LENGTH)]


class _ChangesModel(BaseModel):
    """Keep omission distinct from an explicit clear in nested saved-launch JSON."""

    @model_serializer(mode="wrap")
    def _serialize_changes(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {name: value for name, value in handler(self).items() if name in self.model_fields_set}


class _SimulationFields(_ChangesModel):
    """Shared fields for a simulation snapshot or patch, with explicit omission tracking."""

    model_config = ConfigDict(extra="forbid", strict=True)

    num_turns: int | None = Field(default=None, ge=1)
    adversarial_chat_system_prompt: str | None = None
    simulated_target_system_prompt: str | None = None
    next_message_system_prompt: str | None = None
    final_user_message: str | None = None


class SimulationSettings(_SimulationFields):
    """Displayed simulation settings, not an instruction to change them."""


class SimulationChanges(_SimulationFields):
    """Edits to an existing simulation; omitted fields stay unchanged."""


class _TechniqueFields(_ChangesModel):
    """Shared wire fields; opaque settings remain on the source factory."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    description: str | None = None
    params: dict[_Name, JsonValue] = Field(default_factory=dict, max_length=MAX_ITEMS)
    request_converters: list[_Name] | None = Field(default=None, max_length=MAX_ITEMS)
    response_converters: list[_Name] | None = Field(default=None, max_length=MAX_ITEMS)
    adversarial_chat: _Name | None = None
    adversarial_system_prompt: str | None = None
    adversarial_seed_prompt: str | None = None
    adversarial_prompt_template: str | None = None


class TechniqueSettings(_TechniqueFields):
    """Safe displayed settings; field presence reflects what the source can project."""

    simulation: SimulationSettings | None = None


class TechniqueChanges(_TechniqueFields):
    """Partial changes; omission retains the source and explicit clears are validated."""

    simulation: SimulationChanges | None = None


class TechniqueOverride(BaseModel):
    """A source-pinned edit with dependency and effective identity checks for replay."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_name: _Name
    source_identity: str = Field(min_length=1, max_length=128)
    changes: TechniqueChanges = Field(default_factory=TechniqueChanges)
    dependencies: dict[_Name, str] = Field(default_factory=dict, max_length=MAX_ITEMS)
    effective_identity: str | None = Field(default=None, max_length=128)
