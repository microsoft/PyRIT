# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared scenario dataset inputs and selection capabilities."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from pyrit.models.dataset_limit import normalize_dataset_limit
from pyrit.models.request_limits import MAX_IDENTIFIER_LENGTH, MAX_ITEMS

# Dataset seed filters exposed to clients. Each key is a list-valued ``MemoryInterface.get_seeds``
# keyword argument. Comma-list semantics differ per key because ``get_seeds`` treats each field
# differently (see ``MemoryInterface.get_seeds`` / ``_add_list_conditions``):
#   - harm_categories -> AND + substring: a seed must be tagged with EVERY value, and each value
#     is a substring match (``cyber`` matches ``cyber_harm``).
#   - data_types -> OR + exact: a seed matches ANY value, compared for exact equality.
DATASET_FILTERS: frozenset[str] = frozenset({"harm_categories", "data_types"})
DatasetLimitInputValue = Annotated[
    Annotated[int, Field(ge=1, strict=True)] | Literal["all", "default"],
    BeforeValidator(normalize_dataset_limit),
]


class DatasetFetchPolicy(str, Enum):
    """When scenario preparation may load a dataset."""

    NEVER = "never"
    IF_MISSING = "if_missing"


def _normalize_fetch(value: object) -> object:
    if isinstance(value, str):
        value = value.strip().lower()
    return None if value in (None, "", "default") else value


class DatasetSelectionMode(str, Enum):
    """The permitted named-dataset selection."""

    ANY = "any"
    SUBSET = "subset"
    FIXED = "fixed"


class ScenarioDatasetOptions(BaseModel):
    """Scenario-owned capabilities for dataset overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: DatasetSelectionMode = DatasetSelectionMode.ANY
    allowed_names: list[str] = Field(default_factory=list)
    max_selected: int | None = Field(None, ge=1)
    per_dataset_limit: bool = True
    total_limit: bool = True
    supports_overrides: bool = True
    filters: bool = True
    source_settings: bool = True


class DatasetSourceInput(BaseModel):
    """A named source and optional limit and preparation overrides."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_IDENTIFIER_LENGTH, strict=True)
    max_size: DatasetLimitInputValue = "default"
    fetch: Annotated[DatasetFetchPolicy | None, BeforeValidator(_normalize_fetch)] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Dataset names must be non-empty.")
        return value


class ScenarioDatasetRequest(BaseModel):
    """Serializable dataset overrides shared by all application surfaces."""

    model_config = ConfigDict(extra="forbid")

    sources: list[DatasetSourceInput] | None = Field(None, min_length=1, max_length=MAX_ITEMS)
    max_per_dataset: DatasetLimitInputValue = "default"
    max_total: DatasetLimitInputValue = "default"
    fetch: Annotated[DatasetFetchPolicy | None, BeforeValidator(_normalize_fetch)] = None
    filters: dict[str, list[str]] | None = Field(None, max_length=MAX_ITEMS)

    @field_validator("sources")
    @classmethod
    def _validate_sources(cls, value: list[DatasetSourceInput] | None) -> list[DatasetSourceInput] | None:
        names = [source.name for source in value or []]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate dataset source names are not allowed.")
        return value

    @field_validator("filters")
    @classmethod
    def _validate_filters(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        for key, values in (value or {}).items():
            if key not in DATASET_FILTERS:
                raise ValueError(f"Unknown dataset filter '{key}'. Allowed: {', '.join(sorted(DATASET_FILTERS))}.")
            if len(values) > MAX_ITEMS or any(len(item) > MAX_IDENTIFIER_LENGTH for item in values):
                raise ValueError("Dataset filter values exceed the request limits.")
        return value


class ScenarioDatasetSourceSettings(BaseModel):
    """Effective source settings, not editable request fields."""

    model_config = ConfigDict(extra="forbid")

    name: str
    max_size: Annotated[int, Field(ge=1, strict=True)] | Literal["all"]
    fetch: DatasetFetchPolicy
    configured_provider: bool = False


class ScenarioDatasetSettings(BaseModel):
    """Resolved defaults or effective settings for display and persistence."""

    model_config = ConfigDict(extra="forbid")

    sources: list[ScenarioDatasetSourceSettings] = Field(default_factory=list)
    max_per_dataset: Annotated[int, Field(ge=1, strict=True)] | Literal["all"] = "all"
    max_total: Annotated[int, Field(ge=1, strict=True)] | Literal["all"] = "all"
    fetch: DatasetFetchPolicy = DatasetFetchPolicy.IF_MISSING
    filters: dict[str, list[str]] = Field(default_factory=dict)
